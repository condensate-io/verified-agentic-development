"""End-to-end integration tests for VAD v3 / Horizon pillars (T3-J1)."""

from __future__ import annotations

import hashlib
from pathlib import Path
import sqlite3
import subprocess
from typing import Any

from vad.adapters.domain_pack import DomainKind
from vad.audit.metrics import AutonomyMetricsCalculator
from vad.audit.verify_trail import verify_objective_audit_trail, verify_run_audit_trail
from vad.contracts.intent_hierarchy import (
    ChildEIPRef,
    DecompositionStatus,
    EngineeringObjective,
    Programme,
    StrategicObjective,
)
from vad.contracts.models import AssuranceLevel, RiskTier
from vad.control_plane.intent_hierarchy_service import IntentHierarchyService
from vad.control_plane.remote_worker_coordinator import RemoteWorkerCoordinator
from vad.control_plane.remote_workers import (
    ArtifactTransferManifest,
    RemoteDispatchEnvelope,
    RemoteExecutionReceipt,
    RemoteWorkerIdentity,
    RemoteWorkerPoolPolicy,
    WorkerAuthMethod,
    WorkerExecutionMode,
)
from vad.control_plane.sessions import HostRole, HostSessionManager
from vad.feedback.lesson_review import LessonReviewService
from vad.incident.incident_responder import IncidentSeverity
from vad.memory.contracts import FactType, LessonReviewStatus, MemoryQuery
from vad.memory.gateway import MemoryGateway
from vad.memory.providers.local import LocalMemoryProvider
from vad.memory.providers.null import NullMemoryProvider
from vad.policy.autonomy_matrix import PolicyStage
from vad.release.decision import ReleaseOutcome
from vad.review.dsse_attestation import (
    DSSEAttestor,
    DSSEEnvelope,
    decode_attestation_statement,
)
from vad.server.app import VADApi
from vad.server.db.store import SCHEMA_VERSION, ServerStore
from vad.signing.ed25519_local import LocalEd25519KeyProvider
from vad.workflows.continuous_operations import (
    ContinuousOperationsRequest,
    ContinuousVerifiedOperationsWorkflow,
)
from vad.workflows.domain_pack_execution import (
    DomainExecutionRequest,
    DomainPackExecutionRunner,
)
from vad.workflows.hierarchical_delivery import (
    ChildEIPExecutionSpec,
    HierarchicalDeliveryRequest,
    HierarchicalDeliveryWorkflow,
)
from vad.workflows.verified_delivery import (
    VerifiedDeliveryRequest,
    VerifiedDeliveryWorkflow,
)

_ED25519_TEST_SEED = bytes.fromhex(
    "9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60"
)


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _passing_executor(
    cmd_parts: list[str], **kwargs: Any
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        args=cmd_parts,
        returncode=0,
        stdout=f"ok: {' '.join(cmd_parts)}\n",
        stderr="",
    )


def _make_workspace(tmp_path: Path, name: str = "workspace") -> Path:
    ws = tmp_path / name
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "app.py").write_text(
        "def process_event(valid: bool) -> str:\n"
        "    return 'accepted' if valid else 'rejected'\n",
        encoding="utf-8",
    )
    (ws / "test_app.py").write_text(
        "from app import process_event\n\n"
        "def test_process_event() -> None:\n"
        "    assert process_event(True) == 'accepted'\n",
        encoding="utf-8",
    )
    return ws


def _make_key_provider(tmp_path: Path, key_id: str) -> LocalEd25519KeyProvider:
    return LocalEd25519KeyProvider.from_seed(
        _ED25519_TEST_SEED,
        key_id=key_id,
        key_dir=tmp_path / ".vad" / "keys",
    )


def _assert_dsse_verified(
    envelope: Any,
    key_provider: LocalEd25519KeyProvider,
    *,
    expected_decision: str,
) -> None:
    attestor = DSSEAttestor(key_provider=key_provider, strict=True)
    valid, _, err = attestor.verify(envelope)
    assert valid is True, f"Ed25519 DSSE signature failed: {err}"
    assert envelope.signatures[0].keyid == key_provider.key_id
    stmt = decode_attestation_statement(envelope)
    assert stmt["predicate"]["decision"] == expected_decision


def test_e2e_stage03_hierarchical_intent_programme_delivery_and_independent_audit(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "stage03_hierarchy.sqlite3"
    store = ServerStore(db_path)
    store.migrate()
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION == 13

    hierarchy_service = IntentHierarchyService(store=store)
    key_provider = _make_key_provider(tmp_path, "ed25519-stage03-e2e")
    workspace = _make_workspace(tmp_path)

    strategic = StrategicObjective(
        node_id="so-zero-trust-cutover",
        title="Zero-Trust Service Cutover",
        description="Enforce non-downgradable assurance and verified cutover across services.",
        risk_floor=RiskTier.MEDIUM,
        assurance_floor=AssuranceLevel.A2_INDEPENDENT,
        inherited_invariants=["verifier_not_builder", "signed_dsse_attestation"],
        max_total_cost_usd=100.0,
    )
    programme = Programme(
        node_id="prog-core-cutover",
        title="Core Platform Cutover Programme",
        description="Coordinate schema migration, service API rollout, and final cutover.",
        parent_id=strategic.node_id,
        risk_floor=RiskTier.MEDIUM,
        assurance_floor=AssuranceLevel.A2_INDEPENDENT,
        inherited_invariants=[
            "verifier_not_builder",
            "signed_dsse_attestation",
            "fresh_evidence_required",
        ],
        max_total_cost_usd=100.0,
    )
    objective = EngineeringObjective(
        node_id="eo-service-cutover-01",
        title="Database & Service API Cutover",
        description="Execute DB migration and API rollout in tranche 1, then critical cutover in tranche 2.",
        parent_id=programme.node_id,
        risk_floor=RiskTier.MEDIUM,
        assurance_floor=AssuranceLevel.A2_INDEPENDENT,
        inherited_invariants=[
            "verifier_not_builder",
            "signed_dsse_attestation",
            "fresh_evidence_required",
        ],
        max_total_cost_usd=100.0,
        require_coordinated_release=True,
        child_eips=[
            ChildEIPRef(
                eip_name="eip-db-migration",
                eip_digest=_sha256_hex(b"eip-db-migration"),
                risk_tier=RiskTier.HIGH,
                assurance_level=AssuranceLevel.A3_ATTESTED,
                depends_on=[],
                cost_usd=25.0,
            ),
            ChildEIPRef(
                eip_name="eip-service-api",
                eip_digest=_sha256_hex(b"eip-service-api"),
                risk_tier=RiskTier.MEDIUM,
                assurance_level=AssuranceLevel.A2_INDEPENDENT,
                depends_on=[],
                cost_usd=20.0,
            ),
            ChildEIPRef(
                eip_name="eip-cutover",
                eip_digest=_sha256_hex(b"eip-cutover"),
                risk_tier=RiskTier.CRITICAL,
                assurance_level=AssuranceLevel.A4_RESTRICTED,
                depends_on=["eip-db-migration", "eip-service-api"],
                cost_usd=35.0,
            ),
        ],
    )

    hierarchy_service.register_strategic_objective(strategic)
    hierarchy_service.register_programme(programme)
    hierarchy_service.register_engineering_objective(objective)

    submitted = hierarchy_service.submit_for_approval(objective.node_id)
    assert submitted.decomposition_status == DecompositionStatus.AWAITING_APPROVAL

    approved = hierarchy_service.approve_objective(
        objective.node_id,
        approver="guardian-lead",
        approver_role="release_guardian",
    )
    assert approved.decomposition_status == DecompositionStatus.APPROVED
    assert approved.approved_by == "guardian-lead"

    workflow = HierarchicalDeliveryWorkflow(
        store=store,
        hierarchy_service=hierarchy_service,
        memory_gateway=MemoryGateway(provider=NullMemoryProvider()),
        key_provider=key_provider,
        workspace_dir=workspace,
        command_executor=_passing_executor,
    )

    result = workflow.execute(
        HierarchicalDeliveryRequest(
            objective_id=objective.node_id,
            run_id_prefix="obj-run",
            child_specs={
                "eip-db-migration": ChildEIPExecutionSpec(
                    eip_name="eip-db-migration",
                    change_request="Apply backward-compatible schema migration in app module.",
                    risk_tier=RiskTier.HIGH,
                    domain_tags=["database", "migration"],
                    human_approvers=["guardian-alice"],
                    stage=PolicyStage.DEPLOY,
                    cost_usd=25.0,
                ),
                "eip-service-api": ChildEIPExecutionSpec(
                    eip_name="eip-service-api",
                    change_request="Update service API handler in app module so that new schema fields validate.",
                    risk_tier=RiskTier.MEDIUM,
                    domain_tags=["api"],
                    stage=PolicyStage.CI_CD,
                    cost_usd=20.0,
                ),
                "eip-cutover": ChildEIPExecutionSpec(
                    eip_name="eip-cutover",
                    change_request="Perform production traffic cutover in app module with dual guardian authorization.",
                    risk_tier=RiskTier.CRITICAL,
                    domain_tags=["cutover", "production"],
                    human_approvers=["guardian-alice", "guardian-bob"],
                    explicit_authorization_ref="CAB-2026-CUTOVER-01",
                    stage=PolicyStage.DEPLOY,
                    cost_usd=35.0,
                ),
            },
            cross_eip_proof_callback=lambda obj, child_res: len(child_res) == 3,
        )
    )

    assert result.executed_tranches == [
        ["eip-db-migration", "eip-service-api"],
        ["eip-cutover"],
    ]
    assert result.skipped_blocked_eips == {}
    assert result.cross_eip_proof_passed is True
    assert result.rollup_verified is True
    assert result.rollup_reasons == []
    assert result.release_readiness.rollup_verified is True
    assert result.release_readiness.can_release_objective is True
    assert result.release_readiness.releasable_child_eips == [
        "eip-db-migration",
        "eip-service-api",
        "eip-cutover",
    ]
    assert result.final_objective.decomposition_status == DecompositionStatus.VERIFIED

    assert (
        result.child_results["eip-db-migration"].release_decision.outcome
        == ReleaseOutcome.APPROVED_WITH_HUMAN
    )
    assert (
        result.child_results["eip-service-api"].release_decision.outcome
        == ReleaseOutcome.AUTONOMOUS_RELEASE
    )
    assert (
        result.child_results["eip-cutover"].release_decision.outcome
        == ReleaseOutcome.APPROVED_RESTRICTED
    )

    report = verify_objective_audit_trail(
        store,
        objective.node_id,
        key_provider=key_provider,
    )
    assert report.verified is True
    assert report.failures == []
    assert set(report.child_reports.keys()) == {
        "eip-db-migration",
        "eip-service-api",
        "eip-cutover",
    }

    for eip_name, child_report in report.child_reports.items():
        assert child_report.verified is True, eip_name
        checks_by_name = {c.check: c.passed for c in child_report.checks}
        assert checks_by_name["dsse_attestation"] is True, eip_name
        stored_evidence = store.load_run_evidence(child_report.run_id).evidence
        raw_envelope = stored_evidence.release["dsse_envelope"]
        _assert_dsse_verified(
            DSSEEnvelope.model_validate(raw_envelope),
            key_provider,
            expected_decision="approved",
        )


def test_e2e_scope_b_all_four_builtin_domain_packs_and_core_reuse(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "scope_b_e2e.sqlite3"
    store = ServerStore(db_path)
    store.migrate()
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION == 13

    mem_provider = LocalMemoryProvider(local_path=tmp_path / "scope_b_memory.json")
    gateway = MemoryGateway(provider=mem_provider, fail_open=False)
    key_provider = _make_key_provider(tmp_path, "ed25519-scope-b-e2e")
    runner = DomainPackExecutionRunner(
        store=store,
        memory_gateway=gateway,
        key_provider=key_provider,
    )

    requests = [
        (
            DomainExecutionRequest(
                run_id="run-scope-b-infra",
                domain=DomainKind.INFRASTRUCTURE,
                target_resource="aws_security_group.staging_api",
                summary="Allow staging internal subnet ingress",
                payload={"environment": "staging", "cidr_blocks": ["10.20.0.0/16"]},
                rollback_plan="terraform apply -var-file=previous.tfvars",
                builder_actor="infra-agent-1",
                verifier_actor="infra-verifier-1",
                domain_tags=["team:infra"],
            ),
            ReleaseOutcome.AUTONOMOUS_RELEASE,
            "applied",
        ),
        (
            DomainExecutionRequest(
                run_id="run-scope-b-ops",
                domain=DomainKind.OPERATIONS,
                target_resource="k8s/deployment/read-api",
                summary="Scale staging read replicas to 4",
                payload={"environment": "staging", "action_type": "traffic_shift"},
                rollback_plan="kubectl scale deployment/read-api --replicas=2",
                builder_actor="ops-agent-1",
                verifier_actor="ops-verifier-1",
                domain_tags=["team:sre"],
            ),
            ReleaseOutcome.AUTONOMOUS_RELEASE,
            "executed",
        ),
        (
            DomainExecutionRequest(
                run_id="run-scope-b-biz",
                domain=DomainKind.BUSINESS,
                target_resource="billing/credit/acct-enterprise-09",
                summary="Issue approved enterprise SLA credit",
                payload={
                    "financial_impact_usd": 6500.0,
                    "customer_segment": "enterprise",
                    "policy_reference": "FIN-POL-2026-11",
                },
                rollback_plan="Void unapplied enterprise billing credit memo",
                builder_actor="revops-agent-1",
                verifier_actor="fin-verifier-1",
                human_approvers=["guardian-finance"],
                domain_tags=["team:revops"],
            ),
            ReleaseOutcome.APPROVED_WITH_HUMAN,
            "applied",
        ),
        (
            DomainExecutionRequest(
                run_id="run-scope-b-compliance",
                domain=DomainKind.COMPLIANCE,
                target_resource="control/soc2-cc6-access-review",
                summary="Record SOC2-CC6 quarterly access attestation",
                payload={
                    "control_family": "SOC2-CC6",
                    "framework": "SOC2",
                    "audit_ticket": "AUD-2026-440",
                },
                rollback_plan="Supersede attestation and reopen audit ticket AUD-2026-440",
                builder_actor="compliance-agent-1",
                verifier_actor="audit-verifier-1",
                human_approvers=["guardian-sec", "guardian-compliance"],
                explicit_authorization_ref="CAB-COMP-2026-440",
                domain_tags=["team:security"],
            ),
            ReleaseOutcome.APPROVED_RESTRICTED,
            "attested",
        ),
    ]

    results = []
    for req, expected_outcome, expected_receipt_status in requests:
        res = runner.execute(req)
        results.append(res)

        assert res.validation_errors == []
        assert all(outcome.passed for outcome in res.proof_outcomes)
        assert res.release_decision.can_release is True
        assert res.release_decision.outcome == expected_outcome
        assert res.applied is True
        assert res.apply_receipt is not None
        assert res.apply_receipt["status"] == expected_receipt_status
        assert res.apply_receipt["approved_decision_id"] == res.release_decision.decision_id
        assert res.published_fact_id is not None

        _assert_dsse_verified(
            res.dsse_envelope,
            key_provider,
            expected_decision="approved",
        )

        stored_decisions = store.list_release_decisions(run_id=req.run_id)
        assert len(stored_decisions) == 1
        assert stored_decisions[0].decision_id == res.release_decision.decision_id

    persisted_proposals = store.list_domain_proposal_records()
    assert len(persisted_proposals) == 4
    assert {record.domain for record in persisted_proposals} == {
        "infrastructure",
        "operations",
        "business",
        "compliance",
    }
    assert all(record.can_release and record.applied for record in persisted_proposals)
    assert all(record.apply_receipt is not None for record in persisted_proposals)

    stored_facts = mem_provider.list_facts()
    assert len(stored_facts) == 4
    assert {fact.fact_id for fact in stored_facts} == {
        res.published_fact_id for res in results
    }
    assert all(fact.fact_type == FactType.VERIFIED_DELIVERY for fact in stored_facts)

    for domain in (
        DomainKind.INFRASTRUCTURE,
        DomainKind.OPERATIONS,
        DomainKind.BUSINESS,
        DomainKind.COMPLIANCE,
    ):
        ctx = gateway.retrieve_advisory_context(
            MemoryQuery(
                query_text=domain.value,
                domain_tags=[f"domain:{domain.value}"],
                include_unapproved_lessons=False,
            )
        )
        assert len(ctx.facts) >= 1


def test_e2e_opt_in_remote_worker_dispatch_receipt_and_rbac_release_gate(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "remote_worker_e2e.sqlite3"
    store = ServerStore(db_path)
    store.migrate()
    key_provider = _make_key_provider(tmp_path, "ed25519-remote-e2e")
    workspace = _make_workspace(tmp_path)

    run_id = "run-remote-e2e-01"
    work_item_id = f"wi-{run_id}"

    # Execute underlying verified delivery run so authoritative RunEvidence, ReleaseDecision,
    # checkpoints, and Ed25519 DSSE signature exist for run_id.
    workflow = VerifiedDeliveryWorkflow(
        store=store,
        memory_gateway=MemoryGateway(provider=NullMemoryProvider()),
        key_provider=key_provider,
        workspace_dir=workspace,
        command_executor=_passing_executor,
    )
    delivery_res = workflow.execute(
        VerifiedDeliveryRequest(
            run_id=run_id,
            change_request="Harden remote worker verification in app module.",
            eip_name="eip-remote-worker-hardening",
            risk_tier=RiskTier.HIGH,
            builder_actor="remote-mtls-builder",
            verifier_actor="remote-oidc-verifier",
            human_approvers=["guardian-sec-1"],
            stage=PolicyStage.DEPLOY,
        )
    )
    assert delivery_res.release_decision.can_release is True
    assert delivery_res.audit_report.verified is True

    session_manager = HostSessionManager(signing_secret=b"vad-remote-e2e-session-secret-01")
    policy = RemoteWorkerPoolPolicy(
        mode=WorkerExecutionMode.OPERATOR_HOSTED_REMOTE,
        remote_workers_enabled=True,
        require_operator_intent_for_remote=True,
        allowed_auth_methods=[WorkerAuthMethod.MTLS, WorkerAuthMethod.OIDC],
        rbac_release_guardian_groups=["sec-release-guardians", "platform-approvers"],
    )
    coordinator = RemoteWorkerCoordinator(
        policy=policy,
        session_manager=session_manager,
        store=store,
    )

    builder_worker = RemoteWorkerIdentity(
        worker_id="remote-mtls-builder",
        host_type="builder_runner",
        role=HostRole.BUILDER,
        auth_method=WorkerAuthMethod.MTLS,
        identity_subject="spiffe://vad.local/ns/workers/sa/remote-mtls-builder",
        endpoint_url="https://127.0.0.1:8443/builder",
        cert_or_issuer_ref="vault://pki/vad/remote-mtls-builder",
        allowed_risk_tiers=[RiskTier.LOW, RiskTier.MEDIUM, RiskTier.HIGH],
    )
    verifier_worker = RemoteWorkerIdentity(
        worker_id="remote-oidc-verifier",
        host_type="verifier_runner",
        role=HostRole.VERIFIER,
        auth_method=WorkerAuthMethod.OIDC,
        identity_subject="oidc-sub:vad-remote-oidc-verifier",
        endpoint_url="https://127.0.0.1:8443/verifier",
        cert_or_issuer_ref="https://127.0.0.1:9443/oidc/issuer",
        allowed_risk_tiers=[RiskTier.LOW, RiskTier.MEDIUM, RiskTier.HIGH],
    )

    admitted_builder, builder_reasons = coordinator.register_worker(
        builder_worker,
        operator_intent_ref="op-intent-remote-e2e-01",
    )
    admitted_verifier, verifier_reasons = coordinator.register_worker(
        verifier_worker,
        operator_intent_ref="op-intent-remote-e2e-01",
    )
    assert admitted_builder is True, builder_reasons
    assert admitted_verifier is True, verifier_reasons

    # Dispatch work to OIDC verifier worker with an ArtifactTransferManifest input bundle
    input_payloads = {
        "inputs/patch.diff": b"--- a/app.py\n+++ b/app.py\n@@ -1 +1 @@\n+verified\n",
        "inputs/proof_plan.json": b'{"obligations":["unit","security"]}\n',
    }
    input_manifest = ArtifactTransferManifest(
        transfer_id="xfer-in-remote-e2e-01",
        run_id=run_id,
        work_item_id=work_item_id,
        fence_token=2,
        artifacts={path: _sha256_hex(data) for path, data in input_payloads.items()},
    )
    envelope = RemoteDispatchEnvelope(
        dispatch_id="disp-remote-e2e-01",
        worker_id=verifier_worker.worker_id,
        run_id=run_id,
        work_item_id=work_item_id,
        eip_digest=delivery_res.eip_digest,
        proof_plan_digest=delivery_res.proof_plan_digest,
        risk_tier=RiskTier.HIGH,
        required_role=HostRole.VERIFIER,
        fence_token=2,
        operator_intent_ref="op-intent-remote-e2e-01",
        input_manifest=input_manifest,
    )

    dispatch_session = coordinator.dispatch_work(
        envelope,
        input_payloads=input_payloads,
        builder_actor=builder_worker.worker_id,
    )
    token_ok, token_err = session_manager.verify(
        dispatch_session.session_token,
        required_role=HostRole.VERIFIER,
        expected_work_item_id=work_item_id,
        min_fence_token=2,
    )
    assert token_ok is True, token_err
    assert dispatch_session.session_token.claims.actor == verifier_worker.worker_id

    # Ingest RemoteExecutionReceipt with output bundle
    output_payloads = {
        "outputs/verification_report.json": b'{"passed":true,"isolated":true}\n',
    }
    output_manifest = ArtifactTransferManifest(
        transfer_id="xfer-out-remote-e2e-01",
        run_id=run_id,
        work_item_id=work_item_id,
        fence_token=2,
        artifacts={path: _sha256_hex(data) for path, data in output_payloads.items()},
    )
    receipt = RemoteExecutionReceipt(
        receipt_id="rcpt-remote-e2e-01",
        dispatch_id=envelope.dispatch_id,
        worker_id=verifier_worker.worker_id,
        run_id=run_id,
        work_item_id=work_item_id,
        fence_token=2,
        actor=verifier_worker.worker_id,
        role=HostRole.VERIFIER,
        passed=True,
        summary="Isolated OIDC remote verifier passed all obligations",
        output_manifest=output_manifest,
    )
    receipt_res = coordinator.ingest_execution_receipt(
        receipt,
        output_payloads=output_payloads,
    )
    assert receipt_res.verified is True
    assert receipt_res.reasons == []

    # RBAC release gate: unauthorized group or builder self-approval fails; valid guardian group passes
    denied_rbac = coordinator.authorize_release_with_rbac(
        actor_groups_by_guardian={
            builder_worker.worker_id: ["sec-release-guardians"],
        },
        risk_tier=RiskTier.HIGH,
        builder_actor=builder_worker.worker_id,
        verifier_actor=verifier_worker.worker_id,
    )
    assert denied_rbac.authorized is False
    assert builder_worker.worker_id in denied_rbac.denied_actors

    approved_rbac = coordinator.authorize_release_with_rbac(
        actor_groups_by_guardian={
            "guardian-sec-1": ["sec-release-guardians"],
        },
        risk_tier=RiskTier.HIGH,
        builder_actor=builder_worker.worker_id,
        verifier_actor=verifier_worker.worker_id,
    )
    assert approved_rbac.authorized is True
    assert approved_rbac.approved_guardians == ["guardian-sec-1"]
    assert approved_rbac.denied_actors == []

    # Independent audit trail verification including artifact_transfer_manifests check
    audit_report = verify_run_audit_trail(
        store,
        run_id,
        key_provider=key_provider,
        artifact_payloads={
            input_manifest.transfer_id: input_payloads,
            output_manifest.transfer_id: output_payloads,
        },
        require_transfer_manifests=True,
    )
    assert audit_report.verified is True
    assert audit_report.failures == []
    checks_map = {item.check: item.passed for item in audit_report.checks}
    assert checks_map["artifact_transfer_manifests"] is True


def test_e2e_stage04_continuous_verified_operations_drift_to_learned_prevention(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "stage04_ops_e2e.sqlite3"
    store = ServerStore(db_path)
    store.migrate()
    key_provider = _make_key_provider(tmp_path, "ed25519-stage04-e2e")
    workspace = _make_workspace(tmp_path)
    gateway = MemoryGateway(
        provider=LocalMemoryProvider(local_path=tmp_path / "stage04_memory.json"),
        fail_open=False,
    )

    delivery_workflow = VerifiedDeliveryWorkflow(
        store=store,
        memory_gateway=gateway,
        key_provider=key_provider,
        workspace_dir=workspace,
        command_executor=_passing_executor,
    )

    # 1. Initial accepted release run
    initial_run = delivery_workflow.execute(
        VerifiedDeliveryRequest(
            run_id="run-prod-release-1",
            change_request="Deploy event processor update in app module so that valid events pass.",
            eip_name="eip-event-processor",
            risk_tier=RiskTier.MEDIUM,
            domain_tags=["operations"],
            cost_usd=0.05,
        )
    )
    assert initial_run.release_decision.can_release is True
    assert initial_run.audit_report.verified is True

    # 2. Post-release runtime drift incident referencing originating_run_id="run-prod-release-1"
    ops_workflow = ContinuousVerifiedOperationsWorkflow(
        store=store,
        memory_gateway=gateway,
        key_provider=key_provider,
        workspace_dir=workspace,
        command_executor=_passing_executor,
        audit_sqlite_path=str(tmp_path / "missing_audit.db"),
    )
    remediation = ops_workflow.execute(
        ContinuousOperationsRequest(
            remediation_run_id="run-prod-remediate-1",
            trigger_type="runtime_drift",
            description="Production signing configuration drift detected after event processor rollout",
            originating_run_id="run-prod-release-1",
            severity_hint=IncidentSeverity.SEV1,
            escalate_critical=True,
            stage=PolicyStage.DRIFT,
            domain_tags=["incident", "operations"],
            human_approvers=["guardian-alice", "guardian-bob"],
            explicit_authorization_ref="CAB-2026-DRIFT-01",
            cost_usd=0.10,
        )
    )

    # DefectLinkRecord persisted and AutonomyMetricsCalculator detects 1 escaped defect
    assert remediation.defect_link is not None
    assert remediation.defect_link.run_id == "run-prod-release-1"
    assert remediation.defect_link.evidence_digest == initial_run.evidence_digest
    metrics = AutonomyMetricsCalculator().compute_from_sqlite(store.db_path)
    assert metrics.escaped_defect_count == 1

    # CRITICAL remediation EIP delivered with dual guardian sign-off + explicit_authorization_ref
    assert remediation.seeded_eip.risk_tier == RiskTier.CRITICAL
    assert remediation.seeded_eip.effective_assurance_level() == AssuranceLevel.A4_RESTRICTED
    assert remediation.delivery_result.release_decision.can_release is True
    assert (
        remediation.delivery_result.release_decision.outcome
        == ReleaseOutcome.APPROVED_RESTRICTED
    )
    assert remediation.delivery_result.release_decision.recorded_approvers == [
        "guardian-alice",
        "guardian-bob",
    ]
    assert (
        remediation.delivery_result.release_decision.explicit_authorization_ref
        == "CAB-2026-DRIFT-01"
    )
    assert remediation.delivery_result.audit_report.verified is True

    # 3. Candidate lesson starts PROPOSED, then approved by human guardian via LessonReviewService
    lesson_id = remediation.candidate_lesson.lesson_id
    assert lesson_id == remediation.delivery_result.published_lesson_id
    proposed = store.load_lesson_promotion(lesson_id)
    assert proposed is not None
    assert proposed.review_status == LessonReviewStatus.PROPOSED.value

    review_service = LessonReviewService(store, memory_gateway=gateway)
    approved = review_service.approve_lesson(
        lesson_id,
        reviewer="guardian-carol",
        reviewer_role="release_guardian",
        note="Require regression proof for production drift prevention",
    )
    assert approved.review_status == LessonReviewStatus.APPROVED.value

    # 4. Subsequent run automatically retrieves and applies the approved lesson
    followup_run = delivery_workflow.execute(
        VerifiedDeliveryRequest(
            run_id="run-prod-release-2",
            change_request="Update event processor signing guard in app module so that drift is prevented.",
            eip_name="eip-event-processor-v2",
            risk_tier=RiskTier.MEDIUM,
            domain_tags=["incident", "operations"],
            cost_usd=0.05,
        )
    )
    assert lesson_id in followup_run.applied_lesson_ids
    assert followup_run.audit_report.verified is True
    followup_ev = store.load_run_evidence("run-prod-release-2").evidence
    assert followup_ev.verification is not None
    obligation_ids = {r["obligation_id"] for r in followup_ev.verification.results}
    assert any(ob.startswith("po-lesson-") for ob in obligation_ids)


def test_e2e_vad_api_http_routes_for_horizon_pillars(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    evidence_root = tmp_path / "evidence"
    evidence_root.mkdir(parents=True, exist_ok=True)
    db_path = tmp_path / "vad_api_horizon.sqlite3"
    api = VADApi(evidence_root=evidence_root, db_path=db_path)

    # 1. POST /intent-hierarchy/nodes & GET /intent-hierarchy/nodes
    status, strat_res = api.handle(
        "POST",
        "/intent-hierarchy/nodes",
        {
            "node_id": "so-api-1",
            "level": "strategic_objective",
            "title": "API Governed Trust",
            "description": "Strategic trust floor",
            "risk_floor": "medium",
            "assurance_floor": "A2_INDEPENDENT",
            "inherited_invariants": ["no-self-approval"],
            "max_total_cost_usd": 100.0,
        },
    )
    assert status == 201
    assert strat_res["node"]["node_id"] == "so-api-1"

    status, prog_res = api.handle(
        "POST",
        "/intent-hierarchy/nodes",
        {
            "node_id": "prog-api-1",
            "level": "programme",
            "title": "API Core Programme",
            "description": "Programme beneath strategic objective",
            "parent_id": "so-api-1",
            "risk_floor": "medium",
            "assurance_floor": "A2_INDEPENDENT",
            "inherited_invariants": ["no-self-approval"],
            "max_total_cost_usd": 80.0,
        },
    )
    assert status == 201
    assert prog_res["node"]["parent_id"] == "so-api-1"

    status, obj_res = api.handle(
        "POST",
        "/intent-hierarchy/nodes",
        {
            "node_id": "eo-api-1",
            "level": "engineering_objective",
            "title": "API Objective",
            "description": "Two-tranche API objective",
            "parent_id": "prog-api-1",
            "risk_floor": "medium",
            "assurance_floor": "A2_INDEPENDENT",
            "inherited_invariants": ["no-self-approval"],
            "max_total_cost_usd": 60.0,
            "child_eips": [
                {
                    "eip_name": "eip-schema-v1",
                    "eip_digest": _sha256_hex(b"eip-schema-v1"),
                    "risk_tier": "medium",
                    "assurance_level": "A2_INDEPENDENT",
                    "depends_on": [],
                    "cost_usd": 10.0,
                },
                {
                    "eip_name": "eip-cutover-v1",
                    "eip_digest": _sha256_hex(b"eip-cutover-v1"),
                    "risk_tier": "high",
                    "assurance_level": "A3_ATTESTED",
                    "depends_on": ["eip-schema-v1"],
                    "cost_usd": 15.0,
                },
            ],
        },
    )
    assert status == 201
    assert obj_res["node"]["node_id"] == "eo-api-1"

    status, list_nodes_res = api.handle(
        "GET",
        "/intent-hierarchy/nodes?level=engineering_objective&parent_id=prog-api-1",
    )
    assert status == 200
    assert [n["node_id"] for n in list_nodes_res["nodes"]] == ["eo-api-1"]

    # 2. POST /intent-hierarchy/objectives/<id>/approve & GET /intent-hierarchy/objectives/<id>/rollup
    status, approve_res = api.handle(
        "POST",
        "/intent-hierarchy/objectives/eo-api-1/approve",
        {"approver": "guardian-api-lead", "approver_role": "release_guardian"},
    )
    assert status == 200
    assert approve_res["objective"]["decomposition_status"] == "approved"
    assert approve_res["objective"]["approved_by"] == "guardian-api-lead"

    status, rollup_res = api.handle(
        "GET",
        "/intent-hierarchy/objectives/eo-api-1/rollup",
    )
    assert status == 200
    assert rollup_res["schedule"]["tranches"] == [["eip-schema-v1"], ["eip-cutover-v1"]]
    assert rollup_res["schedule"]["ready_to_dispatch"] == ["eip-schema-v1"]
    assert rollup_res["release_readiness"]["objective_id"] == "eo-api-1"

    # 3. GET /domain-packs & POST /domain-packs/evaluate
    status, packs_res = api.handle("GET", "/domain-packs")
    assert status == 200
    assert packs_res["domains"] == [
        "infrastructure",
        "operations",
        "business",
        "compliance",
    ]
    assert packs_res["proposals"] == []

    status, eval_res = api.handle(
        "POST",
        "/domain-packs/evaluate",
        {
            "run_id": "run-api-domain-infra-1",
            "domain": "infrastructure",
            "target_resource": "aws_security_group.staging_web",
            "summary": "Allow staging internal subnet ingress",
            "payload": {"environment": "staging", "cidr_blocks": ["10.30.0.0/16"]},
            "rollback_plan": "terraform apply -var-file=previous.tfvars",
            "builder_actor": "api-infra-builder",
            "verifier_actor": "api-infra-verifier",
        },
    )
    assert status == 200
    assert eval_res["release_decision"]["can_release"] is True
    assert eval_res["applied"] is True
    assert eval_res["apply_receipt"]["status"] == "applied"

    status, packs_after_res = api.handle("GET", "/domain-packs")
    assert status == 200
    assert len(packs_after_res["proposals"]) == 1
    assert packs_after_res["proposals"][0]["run_id"] == "run-api-domain-infra-1"

    # 4. GET /remote-workers, POST /remote-workers/admit, POST /remote-workers/manifests, GET /remote-workers/manifests
    status, workers_initial = api.handle("GET", "/remote-workers")
    assert status == 200
    assert workers_initial["policy"]["mode"] == "local_first"
    assert workers_initial["workers"] == []

    status, admit_res = api.handle(
        "POST",
        "/remote-workers/admit",
        {
            "policy": {
                "mode": "operator_hosted_remote",
                "remote_workers_enabled": True,
                "require_operator_intent_for_remote": True,
                "allowed_auth_methods": ["mtls", "oidc"],
                "rbac_release_guardian_groups": ["sec-release-guardians"],
            },
            "worker": {
                "worker_id": "api-remote-verifier-1",
                "host_type": "verifier_runner",
                "role": "verifier",
                "auth_method": "oidc",
                "identity_subject": "oidc-sub:api-remote-verifier-1",
                "endpoint_url": "https://127.0.0.1:8443/verifier",
                "cert_or_issuer_ref": "https://127.0.0.1:9443/oidc/issuer",
                "allowed_risk_tiers": ["low", "medium", "high"],
            },
            "operator_intent_ref": "op-intent-api-remote-01",
        },
    )
    assert status == 201
    assert admit_res["admitted"] is True
    assert admit_res["worker"]["worker_id"] == "api-remote-verifier-1"

    status, workers_after = api.handle("GET", "/remote-workers?role=verifier")
    assert status == 200
    assert [w["worker_id"] for w in workers_after["workers"]] == ["api-remote-verifier-1"]

    status, manifest_post = api.handle(
        "POST",
        "/remote-workers/manifests",
        {
            "transfer_id": "xfer-api-01",
            "run_id": "run-api-remote-01",
            "work_item_id": "wi-run-api-remote-01",
            "fence_token": 1,
            "artifacts": {"outputs/report.json": _sha256_hex(b'{"passed":true}')},
        },
    )
    assert status == 201
    assert manifest_post["manifest"]["transfer_id"] == "xfer-api-01"

    status, manifest_list = api.handle(
        "GET",
        "/remote-workers/manifests?run_id=run-api-remote-01&work_item_id=wi-run-api-remote-01",
    )
    assert status == 200
    assert [m["transfer_id"] for m in manifest_list["manifests"]] == ["xfer-api-01"]

    # 5. POST /operations/remediate (with an isolated minimal workspace in tmp_path)
    ops_ws = tmp_path / "ops_api_ws"
    ops_ws.mkdir(parents=True, exist_ok=True)
    (ops_ws / "pytest.ini").write_text(
        "[pytest]\n"
        "markers =\n"
        "    contract: contract tests\n"
        "    security: security tests\n"
        "    performance: performance tests\n"
        "    policy: policy tests\n"
        "    release: release tests\n"
        "    telemetry: telemetry tests\n",
        encoding="utf-8",
    )
    (ops_ws / "test_ops_smoke.py").write_text(
        "import pytest\n\n"
        "@pytest.mark.contract\n"
        "@pytest.mark.security\n"
        "@pytest.mark.performance\n"
        "@pytest.mark.policy\n"
        "@pytest.mark.release\n"
        "@pytest.mark.telemetry\n"
        "def test_ops_smoke() -> None:\n"
        "    assert True\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(ops_ws)

    status, remediate_res = api.handle(
        "POST",
        "/operations/remediate",
        {
            "remediation_run_id": "run-api-remediate-1",
            "trigger_type": "runtime_drift",
            "description": "Runtime drift detected on API gateway config",
            "severity_hint": "SEV-2",
            "escalate_critical": False,
            "stage": "runtime",
            "human_approvers": ["guardian-ops-1"],
        },
    )
    assert status == 200
    assert remediate_res["remediation_run_id"] == "run-api-remediate-1"
    assert remediate_res["delivery_result"]["release_decision"]["can_release"] is True
    assert remediate_res["delivery_result"]["audit_report"]["verified"] is True
