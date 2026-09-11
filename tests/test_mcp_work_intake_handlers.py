import json
import pytest
from pathlib import Path

from vad.adapters.mcp_work_intake import (
    handle_work_intake_claim,
    handle_work_intake_discover,
    handle_work_intake_escalate,
    handle_work_intake_report_progress,
    handle_work_intake_submit_proof,
)
from vad.control_plane.clients import (
    ClientConnectionMode,
    ClientManifest,
    ClientTrustState,
    ClientType,
)
from vad.control_plane.work_items import WorkItem, WorkItemGovernance, WorkItemStatus
from vad.server.api.clients import ClientRegistryService
from vad.server.api.work_items import WorkItemService
from vad.server.db.store import ServerStore


@pytest.fixture
def store_env(tmp_path):
    db_path = tmp_path / "mcp_test.db"
    store = ServerStore(db_path)

    # Register client
    manifest = ClientManifest(
        client_id="claude-code-agent-1",
        display_name="Claude Code Agent",
        client_type=ClientType.CLAUDE_CODE,
        version="1.0.0",
        connection_mode=ClientConnectionMode.MCP,
        supported_capabilities=("developer",),
        workspace_root=tmp_path,
        trust_state=ClientTrustState.TRUSTED,
    )
    ClientRegistryService(store).register(manifest)

    # Provision a queued work item
    wi = WorkItem(
        work_item_id="wi-github-101",
        run_id="run-test",
        title="Implement jitter buffer reordering",
        description="Packets arrive out of order",
        role="developer",
        status=WorkItemStatus.QUEUED,
        governance=WorkItemGovernance(
            effort_type="feature",
            mees_estimate=80,
            token_budget=50000,
        ),
    )
    WorkItemService(store).create(wi, actor="test", role="developer")

    return store, db_path


def test_mcp_discover_and_claim_flow(store_env):
    store, db_path = store_env

    # 1. Discover
    discover_resp = handle_work_intake_discover({"db_path": db_path, "role": "developer"})
    assert discover_resp["isError"] is False
    data = json.loads(discover_resp["content"][0]["text"])
    assert data["count"] == 1
    assert data["work_items"][0]["work_item_id"] == "wi-github-101"

    # 2. Claim
    claim_resp = handle_work_intake_claim({
        "db_path": db_path,
        "work_item_id": "wi-github-101",
        "client_id": "claude-code-agent-1",
    })
    assert claim_resp["isError"] is False
    claim_data = json.loads(claim_resp["content"][0]["text"])
    assert claim_data["status"] == "claimed"
    lease_id = claim_data["lease_id"]
    assert lease_id == "wi-github-101"

    # 3. Report Progress
    prog_resp = handle_work_intake_report_progress({
        "db_path": db_path,
        "work_item_id": "wi-github-101",
        "lease_id": lease_id,
        "message": "Implemented packet sequence comparator",
        "percent_complete": 50,
    })
    assert prog_resp["isError"] is False

    # 4. Submit Proof
    proof_resp = handle_work_intake_submit_proof({
        "db_path": db_path,
        "work_item_id": "wi-github-101",
        "lease_id": lease_id,
        "evidence_digest": "1" * 64,
        "test_summary": "Passed 18 packet reorder tests",
    })
    assert proof_resp["isError"] is False
    proof_data = json.loads(proof_resp["content"][0]["text"])
    assert proof_data["status"] == "verifying"

    # Verify store status is VERIFYING
    reloaded = store.load_work_item("wi-github-101")
    assert reloaded.status == WorkItemStatus.VERIFYING
    assert reloaded.evidence_digest == "1" * 64


def test_mcp_escalate_flow(store_env):
    store, db_path = store_env

    # Claim first
    claim_resp = handle_work_intake_claim({
        "db_path": db_path,
        "work_item_id": "wi-github-101",
        "client_id": "claude-code-agent-1",
    })
    lease_id = json.loads(claim_resp["content"][0]["text"])["lease_id"]

    # Escalate blocker
    esc_resp = handle_work_intake_escalate({
        "db_path": db_path,
        "work_item_id": "wi-github-101",
        "lease_id": lease_id,
        "reason": "Missing upstream telephony SIP specification RFC 9999",
    })
    assert esc_resp["isError"] is False
    esc_data = json.loads(esc_resp["content"][0]["text"])
    assert esc_data["status"] == "blocked"

    reloaded = store.load_work_item("wi-github-101")
    assert reloaded.status == WorkItemStatus.BLOCKED
    assert "Missing upstream" in reloaded.blocked_reason
