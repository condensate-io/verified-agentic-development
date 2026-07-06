import json
import sqlite3
from io import BytesIO
import threading
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from vad.control_plane.events import ControlPlaneEventKind
from vad.control_plane.plugins import PluginInventoryRecord, PluginInventoryReviewState, PluginStatus, PluginTargetClient
from vad.control_plane.work_items import WorkItem, WorkItemStatus
from vad.evidence.bundle import AgentEvidence, EffortEvidence, EvidenceRef, RunEvidence, TokenEvidence, VerificationEvidence
from vad.server.app import VADApi, create_server, make_handler
from vad.server.db.store import DashboardActivity, ProofStreamRecord, ServerStore, TerminalStreamRecord


def make_run_evidence(run_id="run-1"):
    return RunEvidence(
        run_id=run_id,
        created_at="2026-06-30T00:00:00",
        eip=EvidenceRef(path="eip.yaml", digest="abc"),
        proof_plan=EvidenceRef(path="proof.yaml", digest="def"),
        agents=AgentEvidence(builder="builder", verifier="verifier"),
        verification=VerificationEvidence(passed=True),
        effort=EffortEvidence(
            effort_type="feature",
            mees=91,
            policy="pass",
            changed_files=1,
            line_delta=6,
            new_dependencies=0,
            complexity_delta=0,
            maintainability_delta=0,
        ),
        tokens=TokenEvidence(budget=1000, used=100, remaining=900),
        final_decision="passed",
    )


def test_health_endpoint_works(tmp_path):
    status, payload = VADApi(tmp_path).handle("GET", "/health")

    assert status == 200
    assert payload == {"status": "ok"}


def test_api_exposes_runs_and_evidence_read_endpoint(tmp_path):
    evidence = make_run_evidence("run-42")
    (tmp_path / "run-42.json").write_text(json.dumps(evidence.model_dump(mode="json")), encoding="utf-8")
    api = VADApi(tmp_path)

    list_status, list_payload = api.handle("GET", "/runs")
    run_status, run_payload = api.handle("GET", "/runs/run-42/evidence")

    assert list_status == 200
    assert list_payload["runs"][0]["run_id"] == "run-42"
    assert run_status == 200
    assert run_payload["evidence"]["final_decision"] == "passed"
    assert run_payload["evidence_digest"] == list_payload["runs"][0]["evidence_digest"]


def test_api_can_read_runs_from_persistence_store(tmp_path):
    db_path = tmp_path / "vad.sqlite3"
    ServerStore(db_path).save_run_evidence(make_run_evidence("stored-run"))
    api = VADApi(tmp_path / "files", db_path=db_path)

    list_status, list_payload = api.handle("GET", "/runs")
    run_status, run_payload = api.handle("GET", "/runs/stored-run/evidence")

    assert list_status == 200
    assert list_payload["runs"][0]["run_id"] == "stored-run"
    assert run_status == 200
    assert run_payload["evidence"]["run_id"] == "stored-run"


def test_dashboard_api_exposes_activity_and_attribution(tmp_path):
    db_path = tmp_path / "vad.sqlite3"
    store = ServerStore(db_path)
    run = store.save_run_evidence(make_run_evidence("dashboard-run"))
    store.save_dashboard_activity(DashboardActivity(
        activity_id="activity-vscode",
        run_id="dashboard-run",
        kind="provider",
        status="blocked",
        client="VSCode",
        actor="builder",
        role="builder",
        task_id="provider-budget",
        summary="Provider budget requires approval.",
        evidence_digest=run.evidence_digest,
    ))
    api = VADApi(tmp_path / "files", db_path=db_path)

    status, payload = api.handle("GET", "/dashboard")

    assert status == 200
    assert payload["activity"][0]["client"] == "VSCode"
    assert payload["activity"][0]["kind"] == "provider"
    assert payload["status_counts"] == {"blocked": 1}


def test_plugin_status_api_exposes_seeded_states(tmp_path):
    status, payload = VADApi(tmp_path).handle("GET", "/plugins/status")

    assert status == 200
    assert payload["status"] == "seeded"
    assert payload["status_counts"] == {
        "available": 1,
        "failed": 1,
        "installed": 1,
        "needs_review": 1,
    }
    assert {plugin["status"] for plugin in payload["plugins"]} == {
        "installed",
        "available",
        "failed",
        "needs_review",
    }
    assert {plugin["publication_readiness"] for plugin in payload["plugins"]} == {
        "local_ready",
        "dry_run_ready",
        "needs_operator_review",
        "blocked",
    }
    assert all(plugin["local_version"] == plugin["version"] for plugin in payload["plugins"])
    assert {plugin["plugin_id"] for plugin in payload["plugins"]} == {
        "vad-codex-local",
        "vad-claude-code-local",
        "vad-vscode-local",
        "vad-cursor-local",
    }


def test_plugin_status_api_prefers_persisted_inventory(tmp_path):
    db_path = tmp_path / "vad.sqlite3"
    store = ServerStore(db_path)
    store.save_plugin_inventory_record(PluginInventoryRecord(
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
        summary="Codex inventory is approved.",
        action_required=None,
    ))
    api = VADApi(tmp_path / "files", db_path=db_path)

    status, payload = api.handle("GET", "/plugins/status")
    _, dashboard = api.handle("GET", "/dashboard")

    assert status == 200
    assert payload["status"] == "inventory"
    assert payload["plugins"] == [{
        "plugin_id": "vad-codex-local",
        "target_client": "codex",
        "status": "installed",
        "version": "1.0.0",
        "local_version": "1.0.0",
        "publication_readiness": "local_ready",
        "summary": "Codex inventory is approved.",
        "action_required": None,
    }]
    assert dashboard["plugin_status"] == payload["plugins"]


def test_dashboard_api_includes_plugin_status_seed(tmp_path):
    db_path = tmp_path / "vad.sqlite3"
    ServerStore(db_path).save_run_evidence(make_run_evidence("plugin-dashboard-run"))
    api = VADApi(tmp_path / "files", db_path=db_path)

    status, payload = api.handle("GET", "/dashboard")

    assert status == 200
    assert {plugin["status"] for plugin in payload["plugin_status"]} == {
        "installed",
        "available",
        "failed",
        "needs_review",
    }


def test_denied_action_returns_policy_shaped_error(tmp_path):
    status, payload = VADApi(tmp_path).handle("POST", "/actions/approve")

    assert status == 403
    assert payload["decision"]["allow"] is False
    assert payload["decision"]["requires_human"] is True
    assert payload["decision"]["denials"] == ["approval storage is not configured"]


def test_authorized_approval_records_evidence(tmp_path):
    db_path = tmp_path / "vad.sqlite3"
    store = ServerStore(db_path)
    run = store.save_run_evidence(make_run_evidence("approval-run"))
    api = VADApi(tmp_path / "files", db_path=db_path)

    status, payload = api.handle("POST", "/actions/approve", body={
        "run_id": "approval-run",
        "actor": "guardian",
        "actor_role": "release_guardian",
        "action": "approve_release",
        "reason": "proofs passed",
    })
    approval_status, approvals = api.handle("GET", "/runs/approval-run/approvals")

    assert status == 201
    assert payload["approval"]["decision"]["allow"] is True
    assert payload["approval"]["evidence_digest"] == run.evidence_digest
    assert approval_status == 200
    assert approvals["approvals"][0]["actor"] == "guardian"


def test_unauthorized_approval_denied_and_recorded(tmp_path):
    db_path = tmp_path / "vad.sqlite3"
    ServerStore(db_path).save_run_evidence(make_run_evidence("unauthorized-run"))
    api = VADApi(tmp_path / "files", db_path=db_path)

    status, payload = api.handle("POST", "/actions/approve", body={
        "run_id": "unauthorized-run",
        "actor": "observer",
        "actor_role": "observer",
        "action": "approve_release",
    })
    _, approvals = api.handle("GET", "/runs/unauthorized-run/approvals")

    assert status == 403
    assert payload["approval"]["decision"]["allow"] is False
    assert payload["approval"]["decision"]["denials"] == ["approval actor role is not authorized"]
    assert approvals["approvals"][0]["decision"]["allow"] is False


def test_self_approval_denied(tmp_path):
    db_path = tmp_path / "vad.sqlite3"
    ServerStore(db_path).save_run_evidence(make_run_evidence("self-approval-run"))
    api = VADApi(tmp_path / "files", db_path=db_path)

    status, payload = api.handle("POST", "/actions/approve", body={
        "run_id": "self-approval-run",
        "actor": "builder",
        "actor_role": "release_guardian",
        "action": "approve_release",
    })

    assert status == 403
    assert payload["approval"]["decision"]["allow"] is False
    assert payload["approval"]["decision"]["denials"] == ["builder may not approve own run"]


def make_control_plane_event(**overrides):
    event = {
        "event_id": "event-1",
        "sequence": 1,
        "client_id": "codex-local",
        "run_id": "run-events",
        "task_id": "task-1",
        "kind": "heartbeat",
        "status": "active",
        "actor": "codex",
        "role": "builder",
        "summary": "Codex heartbeat.",
    }
    event.update(overrides)
    return event


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


def test_event_ingestion_requires_persistence(tmp_path):
    status, payload = VADApi(tmp_path).handle("POST", "/events", body=make_control_plane_event())

    assert status == 403
    assert payload["decision"]["allow"] is False
    assert payload["decision"]["denials"] == ["event storage is not configured"]


def test_event_ingestion_validates_and_persists_event(tmp_path):
    db_path = tmp_path / "vad.sqlite3"
    api = VADApi(tmp_path / "files", db_path=db_path)

    status, payload = api.handle("POST", "/events", body=make_control_plane_event())
    list_status, events = api.handle("GET", "/events")

    assert status == 201
    assert payload["decision"]["allow"] is True
    assert payload["event"]["event_id"] == "event-1"
    assert list_status == 200
    assert events["events"][0]["kind"] == "heartbeat"


def test_event_ingestion_rejects_invalid_event(tmp_path):
    api = VADApi(tmp_path / "files", db_path=tmp_path / "vad.sqlite3")

    status, payload = api.handle("POST", "/events", body=make_control_plane_event(kind="unknown"))

    assert status == 400
    assert payload["error"] == "invalid_control_plane_event"


def test_duplicate_event_id_returns_conflict(tmp_path):
    api = VADApi(tmp_path / "files", db_path=tmp_path / "vad.sqlite3")
    api.handle("POST", "/events", body=make_control_plane_event())

    status, payload = api.handle("POST", "/events", body=make_control_plane_event(summary="Duplicate."))

    assert status == 409
    assert payload == {"error": "duplicate_control_plane_event", "event_id": "event-1"}


def test_privileged_event_denial_is_persisted_as_policy_denied_event(tmp_path):
    api = VADApi(tmp_path / "files", db_path=tmp_path / "vad.sqlite3")

    status, payload = api.handle("POST", "/events", body=make_control_plane_event(
        event_id="deploy-event-1",
        kind="deployment_event",
        status="active",
        role="observer",
        summary="Apply production deployment.",
    ))
    _, events = api.handle("GET", "/events")

    assert status == 403
    assert payload["decision"]["allow"] is False
    assert "deployment_event requires one of: release_guardian" in payload["decision"]["denials"]
    assert payload["event"]["kind"] == ControlPlaneEventKind.POLICY_DENIED.value
    assert events["events"][0]["kind"] == ControlPlaneEventKind.POLICY_DENIED.value
    assert events["events"][0]["run_id"] == "run-events"


def test_privileged_event_allowed_for_authorized_role(tmp_path):
    api = VADApi(tmp_path / "files", db_path=tmp_path / "vad.sqlite3")

    status, payload = api.handle("POST", "/events", body=make_control_plane_event(
        event_id="approval-recorded-1",
        kind="approval_recorded",
        status="passed",
        actor="guardian",
        role="release_guardian",
        summary="Release approval recorded.",
    ))

    assert status == 201
    assert payload["event"]["kind"] == "approval_recorded"
    assert payload["decision"]["allow"] is True


def test_client_registration_api_registers_lists_and_unregisters_with_evidence(tmp_path):
    api = VADApi(tmp_path / "files", db_path=tmp_path / "vad.sqlite3")

    register_status, register_payload = api.handle("POST", "/clients/register", body=make_client_manifest(tmp_path))
    list_status, list_payload = api.handle("GET", "/clients")
    unregister_status, unregister_payload = api.handle("DELETE", "/clients/codex-local")
    _, events_payload = api.handle("GET", "/events")

    assert register_status == 201
    assert register_payload["client"]["client_id"] == "codex-local"
    assert register_payload["event"]["actor"] == "codex-local"
    assert list_status == 200
    assert list_payload["clients"][0]["manifest"]["display_name"] == "Codex"
    assert list_payload["clients"][0]["status"] == "disconnected"
    assert unregister_status == 200
    assert unregister_payload["event"]["summary"] == "Client Codex unregistered."
    assert [event["actor"] for event in events_payload["events"]] == ["codex-local", "codex-local"]


def test_client_registration_api_handles_duplicate_invalid_and_missing_clients(tmp_path):
    api = VADApi(tmp_path / "files", db_path=tmp_path / "vad.sqlite3")
    api.handle("POST", "/clients/register", body=make_client_manifest(tmp_path))

    duplicate_status, duplicate_payload = api.handle("POST", "/clients/register", body=make_client_manifest(tmp_path))
    invalid_status, invalid_payload = api.handle("POST", "/clients/register", body=make_client_manifest(tmp_path, client_type="unknown"))
    missing_status, missing_payload = api.handle("DELETE", "/clients/missing-client")

    assert duplicate_status == 409
    assert duplicate_payload == {"error": "duplicate_client", "client_id": "codex-local"}
    assert invalid_status == 400
    assert invalid_payload["error"] == "invalid_client_manifest"
    assert missing_status == 404
    assert missing_payload == {"error": "client_not_found", "client_id": "missing-client"}


def test_client_heartbeat_api_records_status_and_stale_events(tmp_path):
    api = VADApi(tmp_path / "files", db_path=tmp_path / "vad.sqlite3")
    api.handle("POST", "/clients/register", body=make_client_manifest(tmp_path))

    heartbeat_status, heartbeat_payload = api.handle(
        "POST",
        "/clients/codex-local/heartbeat",
        body={
            "run_id": "run-heartbeat",
            "task_id": "build",
            "actor": "codex",
            "role": "builder",
            "summary": "Codex is building.",
        },
    )
    list_status, list_payload = api.handle("GET", "/clients")
    stale_status, stale_payload = api.handle("POST", "/clients/stale-scan", body={"stale_after_seconds": -1})
    _, dashboard = api.handle("GET", "/dashboard")

    assert heartbeat_status == 201
    assert heartbeat_payload["heartbeat"]["client_id"] == "codex-local"
    assert heartbeat_payload["client"]["status"] == "active"
    assert list_status == 200
    assert list_payload["clients"][0]["last_task_id"] == "build"
    assert stale_status == 200
    assert stale_payload["stale_clients"][0]["status"] == "stale"
    assert stale_payload["stale_clients"][0]["lost_task_leases"] == []
    assert "Codex" in dashboard["stale_clients"]
    assert dashboard["active_clients"] == [{
        "client_id": "codex-local",
        "display_name": "Codex",
        "client_type": "codex",
        "status": "stale",
        "heartbeat_age_seconds": dashboard["active_clients"][0]["heartbeat_age_seconds"],
        "last_heartbeat_at": dashboard["active_clients"][0]["last_heartbeat_at"],
        "supported_capabilities": ["repo_read", "tool_call"],
        "connection_mode": "mcp",
        "trust_state": "trusted",
        "last_run_id": "run-heartbeat",
        "last_task_id": "build",
        "lost_task_leases": [],
    }]
    assert dashboard["active_clients"][0]["heartbeat_age_seconds"] >= 0


def test_client_heartbeat_api_requires_registered_client(tmp_path):
    api = VADApi(tmp_path / "files", db_path=tmp_path / "vad.sqlite3")

    status, payload = api.handle(
        "POST",
        "/clients/missing-client/heartbeat",
        body={"actor": "codex", "role": "builder"},
    )

    assert status == 404
    assert payload == {"error": "client_not_found", "client_id": "missing-client"}


def test_task_lease_api_acquires_renews_releases_and_lists(tmp_path):
    api = VADApi(tmp_path / "files", db_path=tmp_path / "vad.sqlite3")
    api.handle("POST", "/clients/register", body=make_client_manifest(tmp_path))

    acquire_status, acquire_payload = api.handle("POST", "/leases", body={
        "task_id": "build",
        "run_id": "run-lease",
        "client_id": "codex-local",
        "role": "builder",
        "actor": "builder",
        "ttl_seconds": 30,
    })
    duplicate_status, duplicate_payload = api.handle("POST", "/leases", body={
        "task_id": "build",
        "run_id": "run-lease",
        "client_id": "codex-local",
        "role": "builder",
        "actor": "builder",
    })
    renew_status, renew_payload = api.handle("POST", "/leases/build/renew", body={
        "client_id": "codex-local",
        "ttl_seconds": 60,
    })
    list_status, list_payload = api.handle("GET", "/leases")
    release_status, release_payload = api.handle("POST", "/leases/build/release", body={
        "client_id": "codex-local",
        "reason": "complete",
    })
    _, events_payload = api.handle("GET", "/events")

    assert acquire_status == 201
    assert acquire_payload["lease"]["status"] == "active"
    assert acquire_payload["event"]["kind"] == "task_lease"
    assert duplicate_status == 409
    assert duplicate_payload["decision"]["allow"] is False
    assert renew_status == 200
    assert renew_payload["lease"]["status"] == "active"
    assert list_status == 200
    assert list_payload["leases"][0]["task_id"] == "build"
    assert release_status == 200
    assert release_payload["lease"]["status"] == "released"
    assert [event["kind"] for event in events_payload["events"]].count("task_lease") == 4


def test_dashboard_task_board_columns_include_lease_owner_and_expiry(tmp_path):
    api = VADApi(tmp_path / "files", db_path=tmp_path / "vad.sqlite3")
    api.handle("POST", "/clients/register", body=make_client_manifest(tmp_path))
    api.handle("POST", "/leases", body={
        "task_id": "build",
        "run_id": "run-board",
        "client_id": "codex-local",
        "role": "builder",
        "actor": "builder",
        "ttl_seconds": 60,
    })
    api.handle("POST", "/leases", body={
        "task_id": "proof",
        "run_id": "run-board",
        "client_id": "codex-local",
        "role": "verifier",
        "actor": "verifier",
        "ttl_seconds": 60,
    })
    api.handle("POST", "/leases/proof/release", body={"client_id": "codex-local", "reason": "proof passed"})
    api.handle("POST", "/leases", body={
        "task_id": "deploy",
        "run_id": "run-board",
        "client_id": "codex-local",
        "role": "deployer",
        "actor": "deployer",
        "ttl_seconds": 60,
    })
    api.handle("POST", "/leases/deploy/expire", body={})
    api.handle("POST", "/events", body=make_control_plane_event(
        event_id="blocked-task",
        sequence=20,
        run_id="run-board",
        task_id="policy",
        kind="blocker",
        status="blocked",
        summary="Policy is blocked.",
    ))
    api.handle("POST", "/events", body=make_control_plane_event(
        event_id="needs-human-task",
        sequence=21,
        run_id="run-board",
        task_id="approval",
        kind="approval_requested",
        status="needs_human",
        summary="Approval needs a human.",
    ))

    status, payload = api.handle("GET", "/dashboard")

    assert status == 200
    columns = payload["task_board_columns"]
    assert {status: [task["task_id"] for task in tasks] for status, tasks in columns.items()} == {
        "active": ["build"],
        "blocked": ["policy"],
        "passed": ["proof"],
        "failed": ["deploy"],
        "needs_human": ["approval"],
    }
    assert columns["active"][0]["lease_owner"] == "codex-local"
    assert columns["active"][0]["lease_expires_at"]
    assert payload["task_leases"][0]["lease_owner"] == "codex-local"


def test_dashboard_task_board_columns_project_durable_work_items(tmp_path):
    api = VADApi(tmp_path / "files", db_path=tmp_path / "vad.sqlite3")
    assert api.db is not None
    api.handle("POST", "/clients/register", body=make_client_manifest(tmp_path))
    api.handle("POST", "/leases", body={
        "task_id": "work-assigned",
        "run_id": "run-work-board",
        "client_id": "codex-local",
        "role": "builder",
        "actor": "builder",
        "ttl_seconds": 60,
    })
    for work_item_id, status, assigned_client_id, lease_id in [
        ("work-queued", WorkItemStatus.QUEUED, None, None),
        ("work-assigned", WorkItemStatus.ASSIGNED, "codex-local", "work-assigned"),
        ("work-running", WorkItemStatus.RUNNING, "codex-local", None),
        ("work-blocked", WorkItemStatus.BLOCKED, "codex-local", None),
        ("work-human", WorkItemStatus.WAITING_FOR_HUMAN, "codex-local", None),
        ("work-verify", WorkItemStatus.VERIFYING, "codex-local", None),
        ("work-approved", WorkItemStatus.APPROVED, "codex-local", None),
        ("work-completed", WorkItemStatus.COMPLETED, "codex-local", None),
        ("work-failed", WorkItemStatus.FAILED, "codex-local", None),
        ("work-cancelled", WorkItemStatus.CANCELLED, "codex-local", None),
        ("work-requeued", WorkItemStatus.REQUEUED, None, None),
    ]:
        api.db.save_work_item(WorkItem(
            work_item_id=work_item_id,
            run_id="run-work-board",
            title=f"{status.value} work",
            role="builder",
            priority=10,
            status=status,
            assigned_client_id=assigned_client_id,
            lease_id=lease_id,
            blocked_reason="waiting on operator" if status == WorkItemStatus.BLOCKED else None,
            governance={
                "effort_type": "feature",
                "mees_estimate": 88,
                "token_budget": 1200,
            } if work_item_id == "work-assigned" else None,
        ))

    dashboard_status, payload = api.handle("GET", "/dashboard")

    assert dashboard_status == 200
    columns = payload["task_board_columns"]
    assert {item["task_id"] for item in columns["active"]} >= {
        "work-assigned",
        "work-queued",
        "work-requeued",
        "work-running",
        "work-verify",
    }
    assert {item["task_id"] for item in columns["blocked"]} == {"work-blocked"}
    assert {item["task_id"] for item in columns["needs_human"]} == {"work-human"}
    assert {item["task_id"] for item in columns["passed"]} == {"work-approved", "work-completed"}
    assert {item["task_id"] for item in columns["failed"]} == {"work-cancelled", "work-failed"}
    assigned = [item for item in columns["active"] if item["task_id"] == "work-assigned"][0]
    assert assigned["kind"] == "work_item"
    assert assigned["work_item_id"] == "work-assigned"
    assert assigned["work_item_status"] == "assigned"
    assert assigned["governance"]["mees_estimate"] == 88
    assert assigned["governance"]["token_budget"] == 1200
    assert assigned["lease_owner"] == "codex-local"
    assert assigned["lease_expires_at"]
    blocked = columns["blocked"][0]
    assert blocked["blocked_reason"] == "waiting on operator"
    assert blocked["summary"] == "blocked work"


def test_dashboard_proof_and_terminal_panels_redact_logs_and_link_recovery(tmp_path):
    api = VADApi(tmp_path / "files", db_path=tmp_path / "vad.sqlite3")
    api.handle("POST", "/events", body=make_control_plane_event(
        event_id="proof-start",
        sequence=1,
        run_id="run-proof",
        task_id="unit-proof",
        kind="proof_started",
        status="active",
        role="verifier",
        summary="Proof started with token=raw-secret-value.",
    ))
    api.handle("POST", "/events", body=make_control_plane_event(
        event_id="proof-failed",
        sequence=2,
        run_id="run-proof",
        task_id="unit-proof",
        kind="proof_finished",
        status="failed",
        role="verifier",
        summary="Proof failed with api_key=raw-secret-value and sk-testsecret.",
    ))
    api.handle("POST", "/events", body=make_control_plane_event(
        event_id="proof-recovery",
        sequence=3,
        run_id="run-proof",
        task_id="unit-proof",
        kind="recovery_action",
        status="active",
        role="operator",
        summary="Recovery evidence written.",
    ))

    status, payload = api.handle("GET", "/dashboard")

    assert status == 200
    assert payload["proof_status"] == [{
        "task_id": "unit-proof",
        "run_id": "run-proof",
        "client": "codex-local",
        "client_id": "codex-local",
        "actor": "codex",
        "role": "verifier",
        "status": "failed",
        "started_at": payload["proof_status"][0]["started_at"],
        "finished_at": payload["proof_status"][0]["finished_at"],
        "summary": "Proof failed with api_key=[REDACTED] and secret=[REDACTED].",
        "evidence_digest": None,
        "recovery_event_id": "proof-recovery",
        "recovery_evidence_url": "/runs/run-proof/evidence",
    }]
    terminal_text = json.dumps(payload["terminal_status"])
    assert "raw-secret-value" not in terminal_text
    assert "sk-testsecret" not in terminal_text
    assert "[REDACTED]" in terminal_text


def test_dashboard_prefers_structured_proof_and_terminal_stream_records(tmp_path):
    db_path = tmp_path / "vad.sqlite3"
    store = ServerStore(db_path)
    store.save_proof_stream_record(ProofStreamRecord(
        record_id="proof-structured",
        run_id="run-proof",
        task_id="unit-proof",
        status="failed",
        client="Codex",
        client_id="codex-local",
        actor="verifier",
        role="verifier",
        summary="Structured proof failed with password=raw-secret.",
        started_at="2026-07-03T00:00:00+00:00",
        finished_at="2026-07-03T00:01:00+00:00",
        evidence_digest="a" * 64,
        recovery_event_id="recovery-structured",
        recovery_evidence_url="/runs/run-proof/evidence",
    ))
    store.save_terminal_stream_record(TerminalStreamRecord(
        record_id="terminal-structured",
        event_id="event-terminal",
        run_id="run-proof",
        task_id="unit-proof",
        kind="proof_finished",
        status="failed",
        client="Codex",
        role="verifier",
        summary="Terminal stream saw sk-testsecret.",
        evidence_digest="b" * 64,
    ))
    api = VADApi(tmp_path / "files", db_path=db_path)
    api.handle("POST", "/events", body=make_control_plane_event(
        event_id="proof-event-fallback",
        sequence=1,
        run_id="run-proof",
        task_id="unit-proof",
        kind="proof_finished",
        status="passed",
        summary="Fallback event should not win.",
    ))

    status, payload = api.handle("GET", "/dashboard")

    assert status == 200
    assert payload["proof_status"][0]["record_id"] == "proof-structured"
    assert payload["proof_status"][0]["status"] == "failed"
    assert payload["proof_status"][0]["summary"] == "Structured proof failed with password=[REDACTED]"
    assert payload["proof_status"][0]["recovery_evidence_url"] == "/runs/run-proof/evidence"
    terminal_text = json.dumps(payload["terminal_status"])
    assert "terminal-structured" in terminal_text
    assert "sk-testsecret" not in terminal_text
    assert "[REDACTED]" in terminal_text


def test_task_lease_api_denies_non_holder_and_self_approval_transition(tmp_path):
    api = VADApi(tmp_path / "files", db_path=tmp_path / "vad.sqlite3")
    api.handle("POST", "/clients/register", body=make_client_manifest(tmp_path))
    api.handle("POST", "/leases", body={
        "task_id": "build",
        "run_id": "run-lease",
        "client_id": "codex-local",
        "role": "builder",
        "actor": "builder",
    })

    renew_status, renew_payload = api.handle("POST", "/leases/build/renew", body={
        "client_id": "other-client",
    })
    denied_status, denied_payload = api.handle("POST", "/leases/build/approval-check", body={
        "actor": "builder",
        "role": "release_guardian",
        "action": "approve_release",
    })
    allowed_status, allowed_payload = api.handle("POST", "/leases/build/approval-check", body={
        "actor": "guardian",
        "role": "release_guardian",
        "action": "approve_release",
    })

    assert renew_status == 403
    assert renew_payload["decision"]["denials"] == ["task lease is held by codex-local"]
    assert denied_status == 403
    assert denied_payload["decision"]["denials"] == ["builder cannot approve own work through lease transition"]
    assert allowed_status == 200
    assert allowed_payload["decision"]["allow"] is True


def test_work_item_api_creates_lists_reads_and_assigns_with_scheduler(tmp_path):
    api = VADApi(tmp_path / "files", db_path=tmp_path / "vad.sqlite3")
    api.handle("POST", "/clients/register", body=make_client_manifest(
        tmp_path,
        client_id="codex-local",
        supported_capabilities=["repo_patch"],
        trust_state="trusted",
    ))
    api.handle("POST", "/clients/codex-local/heartbeat", body={"actor": "codex", "role": "builder"})

    create_status, create_payload = api.handle("POST", "/work-items", body={
        "work_item_id": "work-api",
        "run_id": "run-work",
        "title": "Build API work",
        "description": "Exercise local work item API.",
        "role": "builder",
        "requested_capability": "repo_patch",
        "priority": 10,
        "governance": {
            "effort_type": "feature",
            "mees_estimate": 90,
            "token_budget": 3000,
            "approval_required": True,
            "high_risk": True,
            "operator_intent_ref": "operator-intent-1",
            "approval_ref": "approval-1",
        },
        "actor": "operator",
        "client_id": "operator-local",
    })
    list_status, list_payload = api.handle("GET", "/work-items?run_id=run-work&status=queued")
    read_status, read_payload = api.handle("GET", "/work-items/work-api")
    assign_status, assign_payload = api.handle("POST", "/work-items/work-api/assign", body={
        "actor": "operator",
        "role": "operator",
        "client_id": "operator-local",
    })
    _, events_payload = api.handle("GET", "/events")

    assert create_status == 201
    assert create_payload["work_item"]["status"] == "queued"
    assert create_payload["work_item"]["governance"]["mees_estimate"] == 90
    assert create_payload["work_item"]["governance"]["operator_intent_ref"] == "operator-intent-1"
    assert create_payload["event"]["kind"] == "work_item"
    assert list_status == 200
    assert list_payload["work_items"][0]["governance"]["token_budget"] == 3000
    assert [item["work_item_id"] for item in list_payload["work_items"]] == ["work-api"]
    assert read_status == 200
    assert read_payload["work_item"]["title"] == "Build API work"
    assert read_payload["work_item"]["governance"]["approval_required"] is True
    assert assign_status == 200
    assert assign_payload["selected_client_id"] == "codex-local"
    assert assign_payload["work_item"]["status"] == "assigned"
    assert assign_payload["event"]["task_id"] == "work-api"
    assert [event["kind"] for event in events_payload["events"]][-2:] == ["work_item", "work_item"]


def test_work_item_api_transitions_and_policy_denials_are_replayable(tmp_path):
    api = VADApi(tmp_path / "files", db_path=tmp_path / "vad.sqlite3")
    api.handle("POST", "/work-items", body={
        "work_item_id": "work-transition",
        "run_id": "run-work",
        "title": "Transition work",
        "role": "builder",
        "priority": 10,
    })

    block_status, block_payload = api.handle("POST", "/work-items/work-transition/block", body={
        "actor": "builder",
        "role": "builder",
        "client_id": "codex-local",
        "reason": "needs dependency approval",
    })
    requeue_status, requeue_payload = api.handle("POST", "/work-items/work-transition/requeue", body={})
    cancel_status, cancel_payload = api.handle("POST", "/work-items/work-transition/cancel", body={})
    denied_status, denied_payload = api.handle("POST", "/work-items/work-transition/complete", body={})
    missing_status, missing_payload = api.handle("GET", "/work-items/missing-work")
    _, events_payload = api.handle("GET", "/events")

    assert block_status == 200
    assert block_payload["work_item"]["status"] == "blocked"
    assert block_payload["work_item"]["blocked_reason"] == "needs dependency approval"
    assert requeue_status == 200
    assert requeue_payload["work_item"]["status"] == "requeued"
    assert cancel_status == 200
    assert cancel_payload["work_item"]["status"] == "cancelled"
    assert denied_status == 409
    assert denied_payload["decision"]["allow"] is False
    assert denied_payload["event"]["kind"] == "policy_denied"
    assert missing_status == 404
    assert missing_payload == {"error": "work_item_not_found", "work_item_id": "missing-work"}
    assert [event["kind"] for event in events_payload["events"]] == [
        "work_item",
        "work_item",
        "work_item",
        "work_item",
        "policy_denied",
    ]


def test_work_item_api_requires_persistence_and_is_local_only(tmp_path):
    status, payload = VADApi(tmp_path).handle("POST", "/work-items", body={
        "work_item_id": "work-no-db",
        "run_id": "run-work",
        "title": "No DB",
        "role": "builder",
    })

    assert status == 403
    assert payload["decision"]["denials"] == ["work item storage is not configured"]

    api = VADApi(tmp_path)
    handler_class = make_handler(api)

    class StubHandler(handler_class):
        def __init__(self):
            self.path = "/work-items"
            self.client_address = ("203.0.113.10", 54321)
            self.rfile = BytesIO(b"{}")
            self.wfile = BytesIO()
            self.headers = {"Content-Length": "2"}
            self.status = None

        def send_response(self, status):
            self.status = status

        def send_header(self, *_args):
            return None

        def end_headers(self):
            return None

    handler = StubHandler()
    handler.do_POST()

    assert handler.status == 403
    assert b"local_only_route" in handler.wfile.getvalue()


def test_work_item_api_rejects_high_risk_governance_without_approval(tmp_path):
    api = VADApi(tmp_path / "files", db_path=tmp_path / "vad.sqlite3")

    status, payload = api.handle("POST", "/work-items", body={
        "work_item_id": "work-risk",
        "run_id": "run-work",
        "title": "Risky work",
        "role": "builder",
        "governance": {
            "effort_type": "deploy",
            "mees_estimate": 80,
            "token_budget": 1000,
            "high_risk": True,
        },
    })

    assert status == 400
    assert payload["error"] == "invalid_work_item"
    assert "high-risk work requires work-item approval" in payload["detail"]


def test_stale_client_scan_expires_active_task_lease(tmp_path):
    api = VADApi(tmp_path / "files", db_path=tmp_path / "vad.sqlite3")
    assert api.db is not None
    api.handle("POST", "/clients/register", body=make_client_manifest(tmp_path))
    api.handle("POST", "/clients/codex-local/heartbeat", body={"actor": "codex", "role": "builder"})
    api.handle("POST", "/leases", body={
        "task_id": "build",
        "run_id": "run-lease",
        "client_id": "codex-local",
        "role": "builder",
        "actor": "builder",
    })
    for work_item_id, status, lease_id in [
        ("assigned-work", WorkItemStatus.ASSIGNED, None),
        ("running-work", WorkItemStatus.RUNNING, "build"),
        ("blocked-work", WorkItemStatus.BLOCKED, None),
        ("human-work", WorkItemStatus.WAITING_FOR_HUMAN, None),
        ("verify-work", WorkItemStatus.VERIFYING, None),
        ("done-work", WorkItemStatus.COMPLETED, None),
    ]:
        api.db.save_work_item(WorkItem(
            work_item_id=work_item_id,
            run_id="run-lease",
            title=f"{status.value} stale recovery",
            role="builder",
            status=status,
            assigned_client_id="codex-local",
            lease_id=lease_id,
        ))

    stale_status, stale_payload = api.handle("POST", "/clients/stale-scan", body={"stale_after_seconds": -1})
    _, leases_payload = api.handle("GET", "/leases")
    _, dashboard = api.handle("GET", "/dashboard")
    _, events_payload = api.handle("GET", "/events")

    assert stale_status == 200
    assert stale_payload["stale_clients"][0]["lost_task_leases"] == ["build"]
    assert {item["work_item"]["work_item_id"] for item in stale_payload["recovered_work_items"]} == {
        "assigned-work",
        "blocked-work",
        "human-work",
        "running-work",
        "verify-work",
    }
    assert len(stale_payload["recovery_events"]) == 5
    assert leases_payload["leases"][0]["status"] == "expired"
    for work_item_id in ["assigned-work", "blocked-work", "human-work", "running-work", "verify-work"]:
        recovered = api.db.load_work_item(work_item_id)
        assert recovered.status == WorkItemStatus.REQUEUED
        assert recovered.assigned_client_id is None
        assert recovered.lease_id is None
    assert api.db.load_work_item("done-work").status == WorkItemStatus.COMPLETED
    assert "running-work" in {item["task_id"] for item in dashboard["task_board_columns"]["active"]}
    assert {event["kind"] for event in events_payload["events"]} >= {"heartbeat", "task_lease", "work_item", "recovery_action"}


def test_dashboard_replays_from_control_plane_events_when_present(tmp_path):
    api = VADApi(tmp_path / "files", db_path=tmp_path / "vad.sqlite3")
    api.handle("POST", "/events", body=make_control_plane_event(
        event_id="task-started",
        sequence=1,
        task_id="task-dashboard",
        kind="tool_call_started",
        status="active",
        summary="Task started.",
    ))
    api.handle("POST", "/events", body=make_control_plane_event(
        event_id="task-finished",
        sequence=2,
        task_id="task-dashboard",
        kind="tool_call_finished",
        status="passed",
        summary="Task finished.",
    ))
    api.handle("POST", "/events", body=make_control_plane_event(
        event_id="deploy-denied",
        sequence=3,
        task_id="deploy-dashboard",
        kind="deployment_event",
        status="active",
        role="observer",
        summary="Deployment denied.",
    ))

    status, payload = api.handle("GET", "/dashboard")

    assert status == 200
    assert payload["status_counts"] == {"active": 1, "blocked": 1, "passed": 1}
    assert payload["client_counts"] == {"codex-local": 3}
    assert payload["activity"][0]["event_id"] == "task-started"
    assert [event["event_id"] for event in payload["event_timeline"][:2]] == ["task-started", "task-finished"]
    assert payload["event_timeline"][0]["kind"] == "tool_call_started"
    assert payload["event_timeline"][2]["kind"] == "policy_denied"
    assert payload["event_timeline"][2]["summary"] == "Policy denied deployment_event: deployment_event requires one of: release_guardian"
    assert payload["task_board"] == [{
        "task_id": "deploy-dashboard",
        "run_id": "run-events",
        "client": "codex-local",
        "client_id": "codex-local",
        "actor": "codex",
        "role": "observer",
        "status": "blocked",
        "kind": "policy_denied",
        "summary": "Policy denied deployment_event: deployment_event requires one of: release_guardian",
        "updated_at": payload["task_board"][0]["updated_at"],
        "event_id": payload["task_board"][0]["event_id"],
    }, {
        "task_id": "task-dashboard",
        "run_id": "run-events",
        "client": "codex-local",
        "client_id": "codex-local",
        "actor": "codex",
        "role": "builder",
        "status": "passed",
        "kind": "tool_call_finished",
        "summary": "Task finished.",
        "updated_at": payload["task_board"][1]["updated_at"],
        "event_id": "task-finished",
    }]
    assert "plugin_status" in payload
    assert "stale_clients" in payload


def test_dashboard_replay_route_reconstructs_completed_run_from_event_ledger(tmp_path):
    db_path = tmp_path / "vad.sqlite3"
    store = ServerStore(db_path)
    store.save_run_evidence(make_run_evidence("completed-run"))
    store.save_run_evidence(make_run_evidence("other-run"))
    api = VADApi(tmp_path / "files", db_path=db_path)
    api.handle("POST", "/events", body=make_control_plane_event(
        event_id="completed-started",
        sequence=1,
        run_id="completed-run",
        task_id="completed-task",
        kind="tool_call_started",
        status="active",
        summary="Completed task started.",
    ))
    api.handle("POST", "/events", body=make_control_plane_event(
        event_id="completed-proof",
        sequence=2,
        run_id="completed-run",
        task_id="completed-task",
        kind="proof_finished",
        status="passed",
        summary="Completed task proof passed.",
    ))
    api.handle("POST", "/events", body=make_control_plane_event(
        event_id="other-started",
        sequence=3,
        run_id="other-run",
        task_id="other-task",
        kind="tool_call_started",
        status="active",
        summary="Other task started.",
    ))

    status, payload = api.handle("GET", "/dashboard/replay?run_id=completed-run")

    assert status == 200
    assert payload["replay"] == {
        "mode": "event_ledger",
        "run_id": "completed-run",
        "event_count": 2,
        "source": "control_plane_events",
    }
    assert payload["runs"] == [{
        "run_id": "completed-run",
        "final_decision": "passed",
        "evidence_digest": payload["runs"][0]["evidence_digest"],
    }]
    assert [event["event_id"] for event in payload["event_timeline"]] == ["completed-started", "completed-proof"]
    assert payload["task_board"][0]["status"] == "passed"
    assert payload["task_board"][0]["summary"] == "Completed task proof passed."
    assert payload["status_counts"] == {"active": 1, "passed": 1}


def test_dashboard_replay_output_matches_current_snapshot_for_deterministic_fixture(tmp_path):
    db_path = tmp_path / "vad.sqlite3"
    api = VADApi(tmp_path / "files", db_path=db_path)
    ServerStore(db_path).save_run_evidence(make_run_evidence("run-events"))
    for payload in [
        make_control_plane_event(
            event_id="deterministic-started",
            sequence=1,
            task_id="deterministic-task",
            kind="tool_call_started",
            status="active",
            summary="Deterministic task started.",
        ),
        make_control_plane_event(
            event_id="deterministic-finished",
            sequence=2,
            task_id="deterministic-task",
            kind="tool_call_finished",
            status="passed",
            summary="Deterministic task finished.",
        ),
    ]:
        api.handle("POST", "/events", body=payload)

    dashboard_status, dashboard = api.handle("GET", "/dashboard")
    replay_status, replay = api.handle("GET", "/dashboard/replay")

    assert dashboard_status == 200
    assert replay_status == 200
    assert replay == dashboard


def test_dashboard_blocks_on_corrupt_event_payload(tmp_path):
    db_path = tmp_path / "vad.sqlite3"
    api = VADApi(tmp_path / "files", db_path=db_path)
    api.handle("POST", "/events", body=make_control_plane_event())
    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE control_plane_events SET payload_json = '{' WHERE event_id = 'event-1'")

    status, payload = api.handle("GET", "/dashboard")

    assert status == 422
    assert payload["error"] == "invalid_control_plane_event_ledger"
    assert payload["detail"]


def test_event_route_is_local_only(tmp_path):
    api = VADApi(tmp_path)
    handler_class = make_handler(api)

    class StubHandler(handler_class):
        def __init__(self):
            self.path = "/events"
            self.client_address = ("203.0.113.10", 54321)
            self.rfile = BytesIO(b"{}")
            self.wfile = BytesIO()
            self.headers = {"Content-Length": "2"}
            self.status = None

        def send_response(self, status):
            self.status = status

        def send_header(self, *_args):
            return None

        def end_headers(self):
            return None

    handler = StubHandler()
    handler.do_POST()

    assert handler.status == 403
    assert b"local_only_route" in handler.wfile.getvalue()


def test_mcp_http_endpoint_lists_filtered_tools(tmp_path):
    status, payload = VADApi(tmp_path).handle("POST", "/mcp", body={
        "jsonrpc": "2.0",
        "id": 50,
        "method": "tools/list",
        "params": {
            "client_id": "vscode-http",
            "role": "verifier",
        },
    })

    names = {tool["name"] for tool in payload["result"]["tools"]}
    assert status == 200
    assert payload["jsonrpc"] == "2.0"
    assert payload["id"] == 50
    assert "run_proofs" in names
    assert "provider_test" in names
    assert "repo_patch" not in names


def test_mcp_http_endpoint_denies_unapproved_high_risk_tool(tmp_path):
    status, payload = VADApi(tmp_path).handle("POST", "/mcp", body={
        "jsonrpc": "2.0",
        "id": 51,
        "method": "tools/call",
        "params": {
            "name": "repo_patch",
            "arguments": {
                "client_id": "codex-http",
                "role": "builder",
            },
        },
    })

    assert status == 200
    assert payload["id"] == 51
    assert payload["result"]["isError"] is True
    assert "high-risk tool requires explicit approval" in payload["result"]["content"][0]["text"]


def test_mcp_http_route_is_local_only(tmp_path):
    api = VADApi(tmp_path)
    handler_class = make_handler(api)

    class StubHandler(handler_class):
        def __init__(self):
            self.path = "/mcp"
            self.client_address = ("203.0.113.10", 54321)
            self.rfile = BytesIO(b"{}")
            self.wfile = BytesIO()
            self.headers = {"Content-Length": "2"}
            self.status = None

        def send_response(self, status):
            self.status = status

        def send_header(self, *_args):
            return None

        def end_headers(self):
            return None

    handler = StubHandler()
    handler.do_POST()

    assert handler.status == 403
    assert b"local_only_route" in handler.wfile.getvalue()


def test_http_server_smoke(tmp_path):
    evidence = make_run_evidence("run-http")
    db_path = tmp_path / "vad.sqlite3"
    ServerStore(db_path).save_run_evidence(evidence)
    server = create_server("127.0.0.1", 0, tmp_path, db_path=db_path)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_port}"
    try:
        with urlopen(f"{base_url}/health") as response:
            assert json.loads(response.read())["status"] == "ok"

        mcp_body = json.dumps({
            "jsonrpc": "2.0",
            "id": 52,
            "method": "tools/list",
            "params": {"client_id": "generic-http"},
        }).encode("utf-8")
        mcp_request = Request(
            f"{base_url}/mcp",
            data=mcp_body,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urlopen(mcp_request) as response:
            payload = json.loads(response.read())
            assert response.status == 200
            assert payload["id"] == 52
            assert "tools" in payload["result"]

        body = json.dumps({
            "run_id": "run-http",
            "actor": "guardian",
            "actor_role": "release_guardian",
            "action": "approve_release",
        }).encode("utf-8")
        request = Request(
            f"{base_url}/actions/approve",
            data=body,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urlopen(request) as response:
            payload = json.loads(response.read())
            assert response.status == 201
            assert payload["approval"]["decision"]["allow"] is True
    finally:
        server.shutdown()
        thread.join(timeout=2)
