import sqlite3

import pytest

from vad.evidence.bundle import AgentEvidence, EffortEvidence, EvidenceRef, RunEvidence, TokenEvidence, VerificationEvidence
from vad.policy.decisions import PolicyDecision
from vad.control_plane.clients import ClientHeartbeat, ClientManifest, ClientRuntimeStatus
from vad.control_plane.leases import TaskLeaseAcquireRequest, TaskLeaseStatus, new_task_lease
from vad.control_plane.plugins import PluginInventoryRecord, PluginInventoryReviewState, PluginStatus, PluginTargetClient
from vad.server.db.store import (
    ApprovalEvent,
    DashboardActivity,
    ProofStreamRecord,
    SCHEMA_VERSION,
    ServerStore,
    TerminalStreamRecord,
)


def make_run_evidence(run_id="run-db"):
    return RunEvidence(
        run_id=run_id,
        created_at="2026-07-01T00:00:00",
        eip=EvidenceRef(path="eip.yaml", digest="abc"),
        proof_plan=EvidenceRef(path="proof.yaml", digest="def"),
        agents=AgentEvidence(builder="builder", verifier="verifier"),
        verification=VerificationEvidence(passed=True),
        effort=EffortEvidence(
            effort_type="feature",
            mees=90,
            policy="pass",
            changed_files=1,
            line_delta=5,
            new_dependencies=0,
            complexity_delta=0,
            maintainability_delta=0,
        ),
        tokens=TokenEvidence(budget=1000, used=50, remaining=950),
        final_decision="passed",
    )


def test_run_evidence_persists_and_reloads(tmp_path):
    store = ServerStore(tmp_path / "vad.sqlite3")
    saved = store.save_run_evidence(make_run_evidence("run-1"))
    loaded = ServerStore(tmp_path / "vad.sqlite3").load_run_evidence("run-1")

    assert loaded.run_id == "run-1"
    assert loaded.evidence.final_decision == "passed"
    assert loaded.evidence_digest == saved.evidence_digest
    assert store.list_run_evidence()[0].run_id == "run-1"


def test_approval_events_persist_with_actor_and_policy_decision(tmp_path):
    store = ServerStore(tmp_path / "vad.sqlite3")
    run = store.save_run_evidence(make_run_evidence("run-approval"))
    event = ApprovalEvent(
        approval_id="approval-1",
        run_id="run-approval",
        actor="release-guardian",
        action="approve_release",
        decision=PolicyDecision(allow=True, reasons=["approval_recorded"]),
        evidence_digest=run.evidence_digest,
    )

    store.save_approval_event(event)
    loaded = ServerStore(tmp_path / "vad.sqlite3").list_approval_events("run-approval")

    assert loaded == [event]
    assert loaded[0].actor == "release-guardian"
    assert loaded[0].decision.allow is True
    assert loaded[0].evidence_digest == run.evidence_digest


def test_approval_event_requires_existing_run(tmp_path):
    store = ServerStore(tmp_path / "vad.sqlite3")
    event = ApprovalEvent(
        approval_id="approval-missing-run",
        run_id="missing-run",
        actor="release-guardian",
        action="approve_release",
        decision=PolicyDecision(allow=True, reasons=["approval_recorded"]),
    )

    with pytest.raises(sqlite3.IntegrityError):
        store.save_approval_event(event)


def test_dashboard_activity_persists_and_summarizes(tmp_path):
    store = ServerStore(tmp_path / "vad.sqlite3")
    run = store.save_run_evidence(make_run_evidence("dashboard-run"))
    store.save_dashboard_activity(DashboardActivity(
        activity_id="activity-1",
        run_id="dashboard-run",
        kind="swarm",
        status="active",
        client="Claude Code",
        actor="planner",
        role="planner",
        task_id="plan",
        summary="Planner assigned task.",
        evidence_digest=run.evidence_digest,
    ))

    loaded = ServerStore(tmp_path / "vad.sqlite3").list_dashboard_activity("dashboard-run")
    snapshot = store.dashboard_snapshot()

    assert loaded[0].client == "Claude Code"
    assert loaded[0].kind == "swarm"
    assert snapshot["status_counts"] == {"active": 1}
    assert snapshot["kind_counts"] == {"swarm": 1}
    assert snapshot["client_counts"] == {"Claude Code": 1}


def test_client_manifests_register_list_load_and_unregister(tmp_path):
    store = ServerStore(tmp_path / "vad.sqlite3")
    manifest = ClientManifest(
        client_id="codex-local",
        display_name="Codex",
        client_type="codex",
        version="1.0.0",
        connection_mode="mcp",
        supported_capabilities=("repo_read",),
        workspace_root=tmp_path,
        trust_state="trusted",
    )

    store.register_client_manifest(manifest)
    loaded = store.load_client_manifest("codex-local")
    listed = store.list_client_manifests()
    removed = store.unregister_client_manifest("codex-local")

    assert loaded == manifest
    assert listed == [manifest]
    assert removed == manifest
    assert store.list_client_manifests() == []


def test_client_heartbeats_persist_status_and_stale_state(tmp_path):
    store = ServerStore(tmp_path / "vad.sqlite3")
    manifest = ClientManifest(
        client_id="codex-local",
        display_name="Codex",
        client_type="codex",
        version="1.0.0",
        connection_mode="mcp",
        supported_capabilities=("repo_read",),
        workspace_root=tmp_path,
        trust_state="trusted",
    )
    store.register_client_manifest(manifest)

    heartbeat = ClientHeartbeat(
        client_id="codex-local",
        run_id="run-1",
        task_id="build",
        actor="codex",
        role="builder",
    )
    store.record_client_heartbeat(heartbeat)
    active = store.list_client_statuses()[0]
    stale = store.mark_client_stale("codex-local", lost_task_leases=("build",))

    assert active.status == ClientRuntimeStatus.ACTIVE
    assert active.last_run_id == "run-1"
    assert active.last_task_id == "build"
    assert stale.status == ClientRuntimeStatus.STALE
    assert stale.lost_task_leases == ("build",)


def test_task_leases_persist_and_stale_client_expires_active_leases(tmp_path):
    store = ServerStore(tmp_path / "vad.sqlite3")
    manifest = ClientManifest(
        client_id="codex-local",
        display_name="Codex",
        client_type="codex",
        version="1.0.0",
        connection_mode="mcp",
        supported_capabilities=("repo_read",),
        workspace_root=tmp_path,
    )
    store.register_client_manifest(manifest)
    lease = new_task_lease(TaskLeaseAcquireRequest(
        task_id="build",
        run_id="run-1",
        client_id="codex-local",
        role="builder",
        actor="builder",
    ))

    store.save_task_lease(lease)
    loaded = store.load_task_lease("build")
    stale = store.mark_client_stale("codex-local")
    expired = store.load_task_lease("build")

    assert loaded.task_id == "build"
    assert store.list_task_leases(client_id="codex-local")[0].client_id == "codex-local"
    assert stale.lost_task_leases == ("build",)
    assert expired.status == TaskLeaseStatus.EXPIRED
    assert expired.release_reason == "client marked stale"


def test_duplicate_client_manifest_fails_closed(tmp_path):
    store = ServerStore(tmp_path / "vad.sqlite3")
    manifest = ClientManifest(
        client_id="codex-local",
        display_name="Codex",
        client_type="codex",
        version="1.0.0",
        connection_mode="mcp",
        supported_capabilities=("repo_read",),
        workspace_root=tmp_path,
    )

    store.register_client_manifest(manifest)

    with pytest.raises(sqlite3.IntegrityError):
        store.register_client_manifest(manifest)


def test_schema_migration_creates_versioned_tables(tmp_path):
    db_path = tmp_path / "vad.sqlite3"
    store = ServerStore(db_path)

    store.migrate()

    with sqlite3.connect(db_path) as conn:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }

    assert version == SCHEMA_VERSION
    assert {
        "run_evidence",
        "approval_events",
        "dashboard_activity",
        "control_plane_events",
        "client_manifests",
        "client_heartbeats",
        "task_leases",
        "work_items",
        "plugin_inventory",
        "proof_stream_records",
        "terminal_stream_records",
        "diff_proposals",
        "diff_apply_records",
        "run_task_states",
        "operator_intent_records",
    }.issubset(tables)


def test_proof_and_terminal_stream_records_persist_and_filter(tmp_path):
    db_path = tmp_path / "vad.sqlite3"
    store = ServerStore(db_path)
    proof = ProofStreamRecord(
        record_id="proof-1",
        run_id="run-proof",
        task_id="unit-proof",
        status="failed",
        client="Codex",
        client_id="codex-local",
        actor="verifier",
        role="verifier",
        summary="Unit proof failed with token=secret-value.",
        started_at="2026-07-03T00:00:00+00:00",
        finished_at="2026-07-03T00:01:00+00:00",
        evidence_digest="a" * 64,
        recovery_event_id="recovery-1",
        recovery_evidence_url="/runs/run-proof/evidence",
    )
    terminal = TerminalStreamRecord(
        record_id="terminal-1",
        event_id="event-1",
        run_id="run-proof",
        task_id="unit-proof",
        kind="proof_finished",
        status="failed",
        client="Codex",
        role="verifier",
        summary="pytest failed with sk-testsecret.",
        evidence_digest="b" * 64,
    )
    other = TerminalStreamRecord(
        record_id="terminal-2",
        run_id="other-run",
        task_id="other-proof",
        kind="proof_started",
        status="active",
        client="Claude Code",
        role="builder",
        summary="Other proof started.",
    )

    store.save_proof_stream_record(proof)
    store.save_terminal_stream_record(terminal)
    store.save_terminal_stream_record(other)

    loaded_proofs = ServerStore(db_path).list_proof_stream_records(run_id="run-proof")
    loaded_terminal = ServerStore(db_path).list_terminal_stream_records(run_id="run-proof")

    assert loaded_proofs == [proof]
    assert loaded_terminal == [terminal]
    assert loaded_proofs[0].recovery_evidence_url == "/runs/run-proof/evidence"


def test_plugin_inventory_persists_reloads_and_filters(tmp_path):
    db_path = tmp_path / "vad.sqlite3"
    store = ServerStore(db_path)
    codex = PluginInventoryRecord(
        plugin_id="vad-codex-local",
        target_client=PluginTargetClient.CODEX,
        version="1.0.0",
        review_state=PluginInventoryReviewState.APPROVED,
        applied_config_hashes={".codex-plugin/plugin.json": "a" * 64},
        backup_paths=(".codex-plugin/plugin.json.vad-backup",),
        uninstall_status="available",
        rollback_status="ready",
        dashboard_status=PluginStatus.INSTALLED,
        publication_readiness="local_ready",
        summary="Codex inventory approved.",
        artifact_digest="b" * 64,
        manifest_digest="c" * 64,
    )
    vscode = PluginInventoryRecord(
        plugin_id="vad-vscode-local",
        target_client=PluginTargetClient.VS_CODE,
        version="1.0.0",
        review_state=PluginInventoryReviewState.PENDING_REVIEW,
        dashboard_status=PluginStatus.NEEDS_REVIEW,
        summary="VS Code inventory pending review.",
    )

    store.save_plugin_inventory_record(codex)
    store.save_plugin_inventory_record(vscode)
    loaded = ServerStore(db_path).load_plugin_inventory_record("vad-codex-local")
    approved = ServerStore(db_path).list_plugin_inventory_records(review_state="approved")

    assert loaded == codex
    assert loaded.rollback_status == "ready"
    assert loaded.applied_config_hashes[".codex-plugin/plugin.json"] == "a" * 64
    assert [record.plugin_id for record in approved] == ["vad-codex-local"]


def test_newer_schema_fails_closed(tmp_path):
    db_path = tmp_path / "vad.sqlite3"
    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA user_version = 999")

    with pytest.raises(RuntimeError, match="newer than supported"):
        ServerStore(db_path).migrate()
