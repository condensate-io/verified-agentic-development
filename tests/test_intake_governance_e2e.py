import hashlib
import hmac
import json
import pytest
from pathlib import Path

from vad.adapters.mcp_work_intake import (
    handle_work_intake_claim,
    handle_work_intake_discover,
    handle_work_intake_report_progress,
    handle_work_intake_submit_proof,
)
from vad.connectors.base import BaseWorkConnector
from vad.connectors.models import (
    ConnectorConfig,
    ConnectorPlatform,
    ExternalWorkRef,
    WorkIntakePayload,
)
from vad.connectors.registry import get_connector_registry, reset_connector_registry
from vad.connectors.sync_worker import SyncJob, SyncJobStatus, SyncJobType, SyncWorker
from vad.control_plane.clients import (
    ClientConnectionMode,
    ClientManifest,
    ClientTrustState,
    ClientType,
)
from vad.control_plane.work_items import WorkItemStatus
from vad.policy.intake_dor_policy import DoREvaluationStatus, evaluate_intake_dor
from vad.policy.intake_release_gate import (
    ReleaseGateDecision,
    ReleaseSignoff,
    evaluate_intake_release_gate,
)
from vad.server.api.clients import ClientRegistryService
from vad.server.app import VADApi
from vad.server.db.store import ServerStore


class InstrumentedGitHubConnector(BaseWorkConnector):
    def __init__(self, config: ConnectorConfig):
        super().__init__(config)
        self.transitions = []
        self.comments = []
        self.attestations = []

    def validate_webhook(self, headers, body):
        secret = (self.config.webhook_secret or "").encode("utf-8")
        expected = "sha256=" + hmac.new(secret, body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(headers.get("X-Hub-Signature-256", ""), expected)

    def parse_webhook_event(self, headers, payload):
        if payload.get("action") != "opened":
            return None
        issue = payload.get("issue", {})
        repo = payload.get("repository", {})
        ref = ExternalWorkRef(
            platform=ConnectorPlatform.GITHUB,
            instance_url=self.config.instance_url,
            remote_id=str(issue.get("id")),
            remote_key=f"#{issue.get('number')}",
            project_id=repo.get("full_name"),
        )
        return WorkIntakePayload(
            ref=ref,
            title=issue.get("title", ""),
            description=issue.get("body", ""),
            acceptance_criteria=["Bounds check added", "Fuzz testing verified"],
            target_repo=repo.get("full_name"),
            role="developer",
            high_risk=True,
            operator_intent_ref="intent-e2e-1",
        )

    def poll_work_items(self, query=None):
        return []

    def sync_status(self, transition):
        self.transitions.append(transition)
        return True

    def post_comment(self, ref, comment, is_internal=False):
        self.comments.append((ref, comment, is_internal))
        return True

    def attach_attestation(self, attestation):
        self.attestations.append(attestation)
        return True


@pytest.fixture
def e2e_context(tmp_path):
    reset_connector_registry()
    db_path = tmp_path / "e2e_server.db"
    evidence_root = tmp_path / "evidence"
    evidence_root.mkdir()

    webhook_secret = "e2e-secret-key-123"
    cfg = ConnectorConfig(
        name="github",
        platform=ConnectorPlatform.GITHUB,
        instance_url="https://github.com",
        webhook_secret=webhook_secret,
        project_whitelist=["enterprise/telephony"],
    )
    connector = InstrumentedGitHubConnector(cfg)
    reg = get_connector_registry()
    reg.register(connector)

    api = VADApi(evidence_root=evidence_root, db_path=db_path)

    # Pre-register trusted client
    store = ServerStore(db_path)
    manifest = ClientManifest(
        client_id="claude-code",
        display_name="Claude Code Agent",
        client_type=ClientType.CLAUDE_CODE,
        version="1.0.0",
        connection_mode=ClientConnectionMode.MCP,
        supported_capabilities=("developer",),
        workspace_root=tmp_path,
        trust_state=ClientTrustState.TRUSTED,
    )
    ClientRegistryService(store).register(manifest)

    return api, db_path, connector, webhook_secret


def test_full_governed_work_intake_e2e_lifecycle(e2e_context):
    api, db_path, connector, webhook_secret = e2e_context
    evidence_digest = "f" * 64

    # -------------------------------------------------------------
    # Step 1: Inbound Webhook Received & Validated
    # -------------------------------------------------------------
    webhook_payload = {
        "action": "opened",
        "issue": {
            "id": 9901,
            "number": 142,
            "title": "Fix buffer overrun in SIP header parser",
            "body": "Long Via header crashes parser.\\n\\nAcceptance criteria:\\n- [ ] Bounds check added\\n- [ ] Fuzz testing verified",
            "state": "open",
        },
        "repository": {"full_name": "enterprise/telephony"},
    }
    raw_body = json.dumps(webhook_payload).encode("utf-8")
    sig = "sha256=" + hmac.new(webhook_secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    headers = {
        "X-Hub-Signature-256": sig,
        "X-GitHub-Event": "issues",
        "X-GitHub-Delivery": "delivery-e2e-001",
    }

    code, resp = api.handle(
        "POST",
        "/api/v1/webhooks/github",
        body=webhook_payload,
        headers=headers,
        raw_body=raw_body,
    )
    assert code == 201
    assert resp["status"] == "admitted"
    work_item_id = resp["work_item"]["work_item_id"]
    assert work_item_id is not None

    # Verify work item is queued in store
    store = ServerStore(db_path)
    item = store.load_work_item(work_item_id)
    assert item is not None
    assert item.status == WorkItemStatus.QUEUED

    # -------------------------------------------------------------
    # Step 2: Ingress Definition of Ready (DoR) Gate Evaluation
    # -------------------------------------------------------------
    dor_result = evaluate_intake_dor(
        {
            "title": item.title,
            "description": item.description,
            "acceptance_criteria": ["Bounds check added", "Fuzz testing verified"],
        }
    )
    assert dor_result.passed is True
    assert dor_result.status == DoREvaluationStatus.READY

    # -------------------------------------------------------------
    # Step 3: MCP Agent Interaction (Discover, Claim, Progress, Proof)
    # -------------------------------------------------------------
    # 3.1 Discover available work
    discover_res = handle_work_intake_discover({"db_path": db_path, "role": "developer"})
    assert discover_res.get("isError") is False
    discover_data = json.loads(discover_res["content"][0]["text"])
    assert discover_data["count"] >= 1
    assert any(wi["work_item_id"] == work_item_id for wi in discover_data["work_items"])

    # 3.2 Claim work item lease
    claim_res = handle_work_intake_claim(
        {
            "db_path": db_path,
            "work_item_id": work_item_id,
            "client_id": "claude-code",
            "actor": "claude-agent",
            "lease_duration_seconds": 600,
        }
    )
    assert claim_res.get("isError") is False
    claim_data = json.loads(claim_res["content"][0]["text"])
    lease_id = claim_data["lease_id"]
    assert lease_id is not None

    # Work item status is now ASSIGNED / RUNNING
    item = store.load_work_item(work_item_id)
    assert item.status in (WorkItemStatus.ASSIGNED, WorkItemStatus.RUNNING)

    # 3.3 Report progress
    prog_res = handle_work_intake_report_progress(
        {
            "db_path": db_path,
            "work_item_id": work_item_id,
            "lease_id": lease_id,
            "message": "Added bounds check to sip_parser.c; running fuzz suite",
            "percent_complete": 75,
        }
    )
    assert prog_res.get("isError") is False

    # 3.4 Submit proof
    proof_res = handle_work_intake_submit_proof(
        {
            "db_path": db_path,
            "work_item_id": work_item_id,
            "lease_id": lease_id,
            "evidence_digest": evidence_digest,
            "test_summary": "15 unit tests and 500k fuzz iterations passed with 0 crashes",
        }
    )
    assert proof_res.get("isError") is False
    item = store.load_work_item(work_item_id)
    assert item.status == WorkItemStatus.VERIFYING
    assert item.evidence_digest == evidence_digest

    # -------------------------------------------------------------
    # Step 4: Two-Person Release Guardian Gate Evaluation
    # -------------------------------------------------------------
    # Gate check without dual sign-off -> held / needs approval
    gate_before = evaluate_intake_release_gate(item, signoffs=[])
    assert gate_before.decision == ReleaseGateDecision.NEEDS_APPROVAL
    assert gate_before.can_close_remote_ticket is False

    # Dual sign-off: Primary verifier agent + Human release guardian
    signoffs = [
        ReleaseSignoff(actor="claude-agent", role="verifier"),
        ReleaseSignoff(actor="alice-security-lead", role="release_guardian"),
    ]
    gate_after = evaluate_intake_release_gate(item, signoffs=signoffs)
    assert gate_after.decision == ReleaseGateDecision.ALLOWED
    assert gate_after.can_close_remote_ticket is True

    # -------------------------------------------------------------
    # Step 5: Outbound Synchronization to External Platform
    # -------------------------------------------------------------
    sync_worker = SyncWorker(release_gate=None, dor_policy=None)
    ref = ExternalWorkRef(
        platform=ConnectorPlatform.GITHUB,
        instance_url="https://github.com",
        remote_id="9901",
        remote_key="#142",
        project_id="enterprise/telephony",
    )

    # 5.1 Outbound status transition
    status_job = SyncJob(
        job_type=SyncJobType.OUTBOUND_STATUS,
        platform=ConnectorPlatform.GITHUB,
        ref=ref,
        payload={
            "target_status": "Closed",
            "work_item": item.model_dump(mode="json"),
            "signoffs": [s.model_dump(mode="json") for s in signoffs],
            "evidence_digest": evidence_digest,
            "transition_note": "Resolved by AI agent with verified 2-person release guardian approval",
        },
    )
    # 5.2 Outbound proof attestation
    attestation_job = SyncJob(
        job_type=SyncJobType.OUTBOUND_ATTESTATION,
        platform=ConnectorPlatform.GITHUB,
        ref=ref,
        payload={
            "evidence_digest": evidence_digest,
            "test_summary": "15 unit tests passed; fuzz suite 0 crashes",
            "status": "verified",
        },
    )
    sync_worker.enqueue_job(status_job)
    sync_worker.enqueue_job(attestation_job)

    batch = sync_worker.sync_once()
    assert batch.succeeded == 2
    assert batch.failed == 0
    assert batch.held == 0

    # Verify remote connector received status change and attestation
    assert len(connector.transitions) == 1
    assert connector.transitions[0].target_status == "Closed"
    assert len(connector.attestations) == 1
    assert connector.attestations[0].evidence_digest == evidence_digest
