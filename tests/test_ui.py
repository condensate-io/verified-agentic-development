import json
import threading
from urllib.request import urlopen

from vad.evidence.bundle import AgentEvidence, EffortEvidence, EvidenceRef, RunEvidence, TokenEvidence, VerificationEvidence
from vad.server.app import create_server
from vad.server.db.store import DashboardActivity, ServerStore
from vad.ui.build import build_ui
from vad.ui.render import (
    render_active_clients,
    render_approval_result,
    render_dashboard,
    render_evidence_detail,
    render_event_timeline,
    render_kind_view,
    render_plugin_status,
    render_proof_status,
    render_run_list,
    render_task_board,
    render_terminal_status,
)


def make_run_evidence(run_id="ui-run"):
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
            line_delta=4,
            new_dependencies=0,
            complexity_delta=0,
            maintainability_delta=0,
        ),
        tokens=TokenEvidence(budget=1000, used=80, remaining=920),
        final_decision="passed",
    )


def test_ui_build_copies_valid_assets(tmp_path):
    copied = build_ui(tmp_path / "dist")
    names = {path.name for path in copied}
    index = (tmp_path / "dist" / "index.html").read_text(encoding="utf-8")

    assert names == {"index.html", "styles.css", "app.js"}
    assert 'id="run-list"' in index
    assert 'id="evidence-detail"' in index
    assert 'id="approval-panel"' in index
    assert 'id="activity-stream"' in index
    assert 'id="active-clients"' in index
    assert 'id="task-board"' in index
    assert 'id="proof-status"' in index
    assert 'id="terminal-status"' in index
    assert 'id="event-timeline"' in index
    assert 'id="timeline-filter-run"' in index
    assert 'id="timeline-filter-client"' in index
    assert 'id="timeline-filter-status"' in index
    assert 'id="timeline-filter-kind"' in index
    assert 'id="timeline-filter-role"' in index
    assert 'id="swarm-view"' in index
    assert 'id="plugin-status"' in index
    assert 'id="replay-dashboard"' in index


def test_run_list_renders():
    markup = render_run_list([
        {"run_id": "run-1", "final_decision": "passed", "evidence_digest": "abcdef1234567890"}
    ])

    assert 'data-run-id="run-1"' in markup
    assert "passed" in markup
    assert "abcdef123456" in markup


def test_evidence_detail_renders():
    payload = {
        "run_id": "run-1",
        "evidence_digest": "digest",
        "evidence": {
            "final_decision": "passed",
            "agents": {"builder": "alice", "verifier": "bob"},
        },
    }

    markup = render_evidence_detail(payload)

    assert "<h2>run-1</h2>" in markup
    assert "alice" in markup
    assert "bob" in markup
    assert "digest" in markup


def test_denied_approval_path_is_visible():
    markup = render_approval_result({
        "approval": {
            "decision": {
                "allow": False,
                "denials": ["builder may not approve own run"],
            }
        }
    })

    assert "Denied" in markup
    assert "builder may not approve own run" in markup
    assert "approval-result denied" in markup


def dashboard_fixture():
    return {
        "status_counts": {"active": 2, "blocked": 1, "needs_human": 1, "passed": 4},
        "client_counts": {
            "Claude Code": 1,
            "VSCode": 1,
            "Codex": 1,
            "Antigravity": 1,
            "Cursor": 1,
            "Windsurf": 1,
            "OpenCode": 1,
            "Generic MCP": 1,
        },
        "activity": [
            {"activity_id": "a1", "run_id": "run", "kind": "swarm", "status": "active", "client": "Claude Code", "actor": "planner", "role": "planner", "summary": "Planning swarm task.", "task_id": "plan"},
            {"activity_id": "a2", "run_id": "run", "kind": "provider", "status": "blocked", "client": "VSCode", "actor": "builder", "role": "builder", "summary": "Provider budget blocked.", "task_id": "provider-budget"},
            {"activity_id": "a3", "run_id": "run", "kind": "signing", "status": "passed", "client": "Codex", "actor": "auditor", "role": "auditor", "summary": "Signature verified.", "task_id": "sign"},
            {"activity_id": "a4", "run_id": "run", "kind": "deployment", "status": "needs_human", "client": "Antigravity", "actor": "guardian", "role": "release_guardian", "summary": "Deployment needs approval.", "task_id": "deploy"},
            {"activity_id": "a5", "run_id": "run", "kind": "swarm", "status": "passed", "client": "Cursor", "actor": "verifier", "role": "verifier", "summary": "Verifier completed.", "task_id": "verify"},
            {"activity_id": "a6", "run_id": "run", "kind": "provider", "status": "passed", "client": "Windsurf", "actor": "router", "role": "provider", "summary": "Provider route selected.", "task_id": "route"},
            {"activity_id": "a7", "run_id": "run", "kind": "signing", "status": "passed", "client": "OpenCode", "actor": "signer", "role": "signer", "summary": "Evidence signed.", "task_id": "attest"},
            {"activity_id": "a8", "run_id": "run", "kind": "deployment", "status": "active", "client": "Generic MCP", "actor": "deploy", "role": "deployer", "summary": "Rollout monitoring.", "task_id": "rollout"},
        ],
        "event_timeline": [
            {
                "event_id": "event-tool-started",
                "run_id": "run-a",
                "task_id": "build",
                "kind": "tool_call_started",
                "status": "active",
                "client": "Codex",
                "client_id": "codex-local",
                "actor": "builder",
                "role": "builder",
                "summary": "Tool call started.",
                "created_at": "2026-07-03T00:00:00+00:00",
            },
            {
                "event_id": "event-policy-denied",
                "run_id": "run-b",
                "task_id": "deploy",
                "kind": "policy_denied",
                "status": "blocked",
                "client": "VSCode",
                "client_id": "vscode-local",
                "actor": "observer",
                "role": "observer",
                "summary": "Deployment denied by policy.",
                "created_at": "2026-07-03T00:01:00+00:00",
            },
            {
                "event_id": "event-tool-finished",
                "run_id": "run-a",
                "task_id": "build",
                "kind": "tool_call_finished",
                "status": "passed",
                "client": "Codex",
                "client_id": "codex-local",
                "actor": "builder",
                "role": "builder",
                "summary": "Tool call finished.",
                "created_at": "2026-07-03T00:02:00+00:00",
            },
        ],
        "task_board_columns": {
            "active": [
                {"task_id": "build", "summary": "Build in progress.", "kind": "task_lease", "status": "active", "lease_owner": "codex-local", "lease_expires_at": "2026-07-03T00:05:00+00:00"},
            ],
            "blocked": [
                {"task_id": "policy", "summary": "Policy needs review.", "kind": "policy_denied", "status": "blocked", "lease_owner": "vscode-local", "lease_expires_at": "2026-07-03T00:03:00+00:00"},
            ],
            "passed": [
                {"task_id": "proof", "summary": "Proof passed.", "kind": "proof_finished", "status": "passed", "lease_owner": "claude-local", "lease_expires_at": "2026-07-03T00:04:00+00:00"},
            ],
            "failed": [
                {"task_id": "deploy", "summary": "Deployment failed.", "kind": "deployment_event", "status": "failed", "lease_owner": "generic-local", "lease_expires_at": "2026-07-03T00:02:00+00:00"},
            ],
            "needs_human": [
                {"task_id": "approval", "summary": "Approval needed.", "kind": "approval_requested", "status": "needs_human", "lease_owner": "guardian-local", "lease_expires_at": "2026-07-03T00:06:00+00:00"},
            ],
        },
        "proof_status": [
            {
                "task_id": "unit-proof",
                "run_id": "run-a",
                "client": "Codex",
                "status": "failed",
                "started_at": "2026-07-03T00:00:00+00:00",
                "finished_at": "2026-07-03T00:02:00+00:00",
                "summary": "Proof failed with token=[REDACTED].",
                "recovery_evidence_url": "/runs/run-a/evidence",
            },
            {
                "task_id": "integration-proof",
                "run_id": "run-a",
                "client": "Claude Code",
                "status": "passed",
                "started_at": "2026-07-03T00:03:00+00:00",
                "finished_at": "2026-07-03T00:05:00+00:00",
                "summary": "Proof passed.",
            },
        ],
        "terminal_status": [
            {
                "event_id": "proof-failed",
                "run_id": "run-a",
                "task_id": "unit-proof",
                "kind": "proof_finished",
                "status": "failed",
                "client": "Codex",
                "role": "verifier",
                "created_at": "2026-07-03T00:02:00+00:00",
                "summary": "Proof failed with token=[REDACTED].",
            },
        ],
        "active_clients": [
            {
                "client_id": "codex-local",
                "display_name": "Codex",
                "client_type": "codex",
                "status": "active",
                "heartbeat_age_seconds": 4,
                "supported_capabilities": ["repo_read", "tool_call"],
                "connection_mode": "mcp",
            },
            {
                "client_id": "cursor-local",
                "display_name": "Cursor",
                "client_type": "cursor",
                "status": "stale",
                "heartbeat_age_seconds": 181,
                "supported_capabilities": ["repo_read"],
                "connection_mode": "plugin",
            },
            {
                "client_id": "vscode-local",
                "display_name": "VS Code",
                "client_type": "vscode",
                "status": "disconnected",
                "heartbeat_age_seconds": None,
                "supported_capabilities": ["tool_call"],
                "connection_mode": "mcp",
            },
        ],
        "plugin_status": [
            {"plugin_id": "vad-codex-local", "target_client": "codex", "status": "installed", "version": "1.0.0", "local_version": "1.0.0", "publication_readiness": "local_ready", "summary": "Codex local plugin installed."},
            {"plugin_id": "vad-claude-code-local", "target_client": "claude_code", "status": "available", "version": "1.0.0", "local_version": "1.0.0", "publication_readiness": "dry_run_ready", "summary": "Claude Code plugin available."},
            {"plugin_id": "vad-vscode-local", "target_client": "vscode", "status": "needs_review", "version": "1.0.0", "local_version": "1.0.0", "publication_readiness": "needs_operator_review", "summary": "VS Code plugin needs review.", "action_required": "Review workspace settings."},
            {"plugin_id": "vad-cursor-local", "target_client": "cursor", "status": "failed", "version": "1.0.0", "local_version": "1.0.0", "publication_readiness": "blocked", "summary": "Cursor plugin failed validation."},
        ],
    }


def test_dashboard_renders_status_activity_and_work_items():
    markup = render_dashboard(dashboard_fixture())

    assert "Activity" in markup
    assert "Work Items" in markup
    assert "Claude Code" in markup
    assert "VSCode" in markup
    assert "Provider budget blocked." in markup
    assert "Plugins" in markup
    assert "vad-codex-local" in markup
    assert "needs_review" in markup
    assert "Active Clients" in markup
    assert "Task Board" in markup
    assert "Proofs" in markup
    assert "Terminal" in markup
    assert "Event Timeline" in markup
    assert "Tool call started." in markup
    assert "Deployment denied by policy." in markup
    assert 'data-client-status="stale"' in markup
    assert "181s ago" in markup
    assert "repo_read, tool_call" in markup


def test_task_board_renders_columns_lease_owner_and_expiry():
    markup = render_task_board(dashboard_fixture()["task_board_columns"])

    for status in ["active", "blocked", "passed", "failed", "needs_human"]:
        assert f'data-task-status="{status}"' in markup
    assert "Owner codex-local" in markup
    assert "Expires 2026-07-03T00:05:00+00:00" in markup
    assert "Approval needed." in markup


def test_proof_and_terminal_status_render_recovery_links_and_redacted_logs():
    fixture = dashboard_fixture()
    proof_markup = render_proof_status(fixture["proof_status"])
    terminal_markup = render_terminal_status(fixture["terminal_status"])

    assert 'data-proof-status="failed"' in proof_markup
    assert "Recovery evidence" in proof_markup
    assert "/runs/run-a/evidence" in proof_markup
    assert 'data-terminal-kind="proof_finished"' in terminal_markup
    assert "[REDACTED]" in terminal_markup
    assert "raw-secret" not in proof_markup
    assert "raw-secret" not in terminal_markup


def test_active_clients_render_stale_highlight_and_connection_fields():
    markup = render_active_clients(dashboard_fixture()["active_clients"])

    assert "Codex" in markup
    assert "codex / mcp" in markup
    assert "Cursor" in markup
    assert 'client-row stale' in markup
    assert "no heartbeat" in markup


def test_event_timeline_filters_by_run_client_status_kind_and_role():
    events = dashboard_fixture()["event_timeline"]

    assert "Tool call started." in render_event_timeline(events, run_id="run-a")
    assert "Deployment denied by policy." not in render_event_timeline(events, run_id="run-a")
    assert "Deployment denied by policy." in render_event_timeline(events, client="VSCode")
    assert "Deployment denied by policy." in render_event_timeline(events, status="blocked")
    assert "Tool call finished." in render_event_timeline(events, kind="tool_call_finished")
    assert "Tool call started." in render_event_timeline(events, role="builder")
    assert "No events match" in render_event_timeline(events, role="release_guardian")


def test_event_timeline_marks_tool_calls_and_policy_denials_visible():
    markup = render_event_timeline(dashboard_fixture()["event_timeline"])

    assert 'data-event-kind="tool_call_started"' in markup
    assert 'data-event-kind="tool_call_finished"' in markup
    assert 'data-event-kind="policy_denied"' in markup
    assert 'data-event-status="blocked"' in markup


def test_plugin_status_renders_all_seed_states():
    markup = render_plugin_status(dashboard_fixture()["plugin_status"])

    for status in ["installed", "available", "failed", "needs_review"]:
        assert status in markup
    assert "local 1.0.0" in markup
    assert "Publication local_ready" in markup
    assert "Publication needs_operator_review" in markup
    assert "Review workspace settings." in markup


def test_kind_views_render_requested_surfaces():
    activity = dashboard_fixture()["activity"]

    assert "Planning swarm task." in render_kind_view(activity, "swarm")
    assert "Provider route selected." in render_kind_view(activity, "provider")
    assert "Signature verified." in render_kind_view(activity, "signing")
    assert "Rollout monitoring." in render_kind_view(activity, "deployment")


def test_server_serves_built_ui(tmp_path):
    ui_root = tmp_path / "dist"
    build_ui(ui_root)
    db_path = tmp_path / "vad.sqlite3"
    store = ServerStore(db_path)
    run = store.save_run_evidence(make_run_evidence("ui-run"))
    store.save_dashboard_activity(DashboardActivity(
        activity_id="activity-ui",
        run_id="ui-run",
        kind="swarm",
        status="active",
        client="Claude Code",
        actor="planner",
        role="planner",
        task_id="plan",
        summary="Planner active.",
        evidence_digest=run.evidence_digest,
    ))
    server = create_server("127.0.0.1", 0, tmp_path / "evidence", db_path=db_path, ui_root=ui_root)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base_url = f"http://127.0.0.1:{server.server_port}"
        with urlopen(f"{base_url}/") as response:
            index = response.read().decode("utf-8")
            assert response.headers["Content-Type"] == "text/html; charset=utf-8"
            assert 'id="run-list"' in index
        with urlopen(f"{base_url}/app.js") as response:
            script = response.read().decode("utf-8")
            assert "loadRuns" in script
        with urlopen(f"{base_url}/runs") as response:
            payload = json.loads(response.read())
            assert payload["runs"][0]["run_id"] == "ui-run"
        with urlopen(f"{base_url}/dashboard") as response:
            payload = json.loads(response.read())
            assert payload["activity"][0]["client"] == "Claude Code"
            assert {plugin["status"] for plugin in payload["plugin_status"]} == {
                "installed",
                "available",
                "failed",
                "needs_review",
            }
    finally:
        server.shutdown()
        thread.join(timeout=2)
