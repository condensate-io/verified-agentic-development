from pathlib import Path

from vad.control_plane.events import ControlPlaneEvent
from vad.control_plane.governance_records import OperatorIntentRecord
from vad.control_plane.plugins import (
    PluginInventoryRecord,
    PluginInventoryReviewState,
    PluginStatus,
    PluginTargetClient,
    merge_plugin_status_with_events,
)
from vad.control_plane.work_items import WorkItem, WorkItemGovernance, WorkItemStatus
from vad.server.app import VADApi
from vad.server.db.store import SCHEMA_VERSION, ServerStore


def make_client_manifest(tmp_path, **overrides):
    manifest = {
        "client_id": "codex-local",
        "display_name": "Codex",
        "client_type": "codex",
        "version": "1.0.0",
        "connection_mode": "mcp",
        "supported_capabilities": ["repo_read", "tool_call"],
        "workspace_root": str(tmp_path),
        "trust_state": "trusted",
    }
    manifest.update(overrides)
    return manifest


def test_schema_migration_010_adds_hardening_tables(tmp_path):
    db_path = tmp_path / "vad.sqlite3"
    ServerStore(db_path).migrate()

    import sqlite3

    with sqlite3.connect(db_path) as conn:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }

    assert version == SCHEMA_VERSION
    assert {
        "diff_proposals",
        "diff_apply_records",
        "run_task_states",
        "operator_intent_records",
    }.issubset(tables)


def test_stale_scan_auto_reassigns_recovered_work_to_active_client(tmp_path):
    import sqlite3

    api = VADApi(tmp_path / "files", db_path=tmp_path / "vad.sqlite3")
    assert api.db is not None
    api.handle("POST", "/clients/register", body=make_client_manifest(tmp_path))
    api.handle("POST", "/clients/register", body=make_client_manifest(
        tmp_path,
        client_id="vscode-local",
        display_name="VS Code",
        client_type="vscode",
    ))
    api.handle("POST", "/clients/codex-local/heartbeat", body={"actor": "codex", "role": "builder"})
    api.handle("POST", "/clients/vscode-local/heartbeat", body={"actor": "vscode", "role": "builder"})
    with sqlite3.connect(tmp_path / "vad.sqlite3") as conn:
        conn.execute(
            "UPDATE client_heartbeats SET last_heartbeat_at = ? WHERE client_id = ?",
            ("2026-07-01T00:00:00+00:00", "codex-local"),
        )
    api.db.save_work_item(WorkItem(
        work_item_id="recover-me",
        run_id="run-recover",
        title="Recover and reassign",
        role="builder",
        status=WorkItemStatus.ASSIGNED,
        assigned_client_id="codex-local",
    ))

    stale_status, stale_payload = api.handle(
        "POST",
        "/clients/stale-scan",
        body={"stale_after_seconds": 120, "auto_reassign": True},
    )

    assert stale_status == 200
    assert len(stale_payload["stale_clients"]) == 1
    assert stale_payload["stale_clients"][0]["manifest"]["client_id"] == "codex-local"
    assert stale_payload["recovered_work_items"][0]["work_item"]["status"] == "requeued"
    assert stale_payload["reassigned_work_items"][0]["selected_client_id"] == "vscode-local"
    assert stale_payload["reassigned_work_items"][0]["work_item"]["status"] == "assigned"
    assert api.db.load_work_item("recover-me").status == WorkItemStatus.ASSIGNED
    assert api.db.load_run_task_state("run-recover", "recover-me").status == WorkItemStatus.ASSIGNED


def test_plugin_status_merges_inventory_with_control_plane_events(tmp_path):
    db_path = tmp_path / "vad.sqlite3"
    store = ServerStore(db_path)
    store.save_plugin_inventory_record(PluginInventoryRecord(
        plugin_id="vad-codex-local",
        target_client=PluginTargetClient.CODEX,
        version="1.0.0",
        review_state=PluginInventoryReviewState.APPROVED,
        dashboard_status=PluginStatus.NEEDS_REVIEW,
        summary="Awaiting operator review.",
    ))
    store.append_control_plane_event(ControlPlaneEvent(
        sequence=1,
        client_id="operator-local",
        task_id="vad-codex-local",
        kind="approval_recorded",
        status="passed",
        actor="operator",
        role="operator",
        summary="Operator approved vad-codex-local plugin review.",
    ))
    api = VADApi(tmp_path / "files", db_path=db_path)

    status, payload = api.handle("GET", "/plugins/status")

    assert status == 200
    assert payload["status"] == "inventory_events"
    assert payload["plugins"][0]["last_event_id"] is not None
    assert payload["plugins"][0]["publication_readiness"] == "local_ready"


def test_diff_proposal_api_persists_and_replays_apply_records(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "src.txt"
    target.write_text("before\n", encoding="utf-8")
    api = VADApi(workspace, db_path=tmp_path / "vad.sqlite3")
    patch_text = "\n".join([
        "--- a/src.txt",
        "+++ b/src.txt",
        "@@ -1 +1 @@",
        "-before",
        "+after",
        "",
    ])

    create_status, create_payload = api.handle("POST", "/diff-proposals", body={
        "run_id": "run-diff",
        "task_id": "task-diff",
        "patch_text": patch_text,
        "changed_files": ["src.txt"],
        "summary": "Update src.txt",
    })
    proposal_id = create_payload["proposal"]["proposal_id"]
    apply_status, apply_payload = api.handle(
        "POST",
        f"/diff-proposals/{proposal_id}/apply",
        body={
            "workspace_root": str(workspace),
            "verifier_decision": {"allow": True, "reasons": ["verified"]},
            "release_guardian_decision": {"allow": True, "reasons": ["approved"]},
        },
    )
    read_status, read_payload = api.handle("GET", f"/diff-proposals/{proposal_id}")

    assert create_status == 201
    assert apply_status == 200
    assert apply_payload["apply_record"]["applied"] is True
    assert target.read_text(encoding="utf-8") == "after\n"
    assert read_status == 200
    assert read_payload["apply_records"][0]["applied"] is True


def test_work_item_create_syncs_run_task_state_and_governance_summary(tmp_path):
    api = VADApi(tmp_path / "files", db_path=tmp_path / "vad.sqlite3")
    api.handle("POST", "/work-items", body={
        "work_item_id": "governed-work",
        "run_id": "run-gov",
        "title": "Governed work",
        "role": "builder",
        "governance": {
            "effort_type": "feature",
            "mees_estimate": 88,
            "token_budget": 1200,
            "approval_required": True,
            "operator_intent_ref": "intent-1",
        },
    })
    _, dashboard = api.handle("GET", "/dashboard")
    _, states = api.handle("GET", "/run-task-states")

    assert dashboard["governance_summary"]["work_item_count"] == 1
    assert dashboard["governance_summary"]["average_mees"] == 88.0
    assert states["run_task_states"][0]["task_id"] == "governed-work"
    assert states["run_task_states"][0]["status"] == "queued"


def test_operator_intent_records_persist_and_list(tmp_path):
    api = VADApi(tmp_path / "files", db_path=tmp_path / "vad.sqlite3")
    create_status, create_payload = api.handle("POST", "/operator-intents", body={
        "intent_ref": "intent-live-run",
        "actor": "operator",
        "role": "operator",
        "scope": "live-service-tests",
        "summary": "Operator approved one local live-service test run.",
        "granted_tools": ["deploy_local"],
        "live_service_opt_in": True,
        "high_risk": True,
    })
    list_status, list_payload = api.handle("GET", "/operator-intents")
    show_status, show_payload = api.handle("GET", "/operator-intents/intent-live-run")

    assert create_status == 201
    assert list_status == 200
    assert list_payload["operator_intents"][0]["intent_ref"] == "intent-live-run"
    assert show_status == 200
    assert show_payload["operator_intent"]["granted_tools"] == ["deploy_local"]


def test_merge_plugin_status_with_events_marks_failed_review(tmp_path):
    inventory = [PluginInventoryRecord(
        plugin_id="vad-vscode-local",
        target_client=PluginTargetClient.VS_CODE,
        version="1.0.0",
        review_state=PluginInventoryReviewState.REVIEWED,
        dashboard_status=PluginStatus.NEEDS_REVIEW,
        summary="Needs review.",
    )]
    events = [ControlPlaneEvent(
        sequence=1,
        client_id="operator-local",
        task_id="vad-vscode-local",
        kind="deployment_event",
        status="failed",
        actor="operator",
        role="operator",
        summary="Plugin vad-vscode-local install validation failed.",
    )]

    merged = merge_plugin_status_with_events(inventory, events)

    assert merged[0]["status"] == "failed"
    assert merged[0]["event_derived_status"] == "failed"
