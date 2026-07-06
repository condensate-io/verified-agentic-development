import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
import json
from urllib.error import HTTPError
from urllib.request import Request
from urllib.request import urlopen

from vad.server.db.store import ServerStore
from vad.server.fixtures import (
    seed_demo_store,
    seed_level3_demo_store,
    seed_multi_client_role_separation_store,
    seed_multi_client_simulator_store,
)
from vad.server.serve import prepare_ui_server


def test_compose_file_defines_vad_ui_service():
    text = Path("compose.yaml").read_text(encoding="utf-8")

    assert "vad-ui:" in text
    assert "vad.server.serve" in text
    assert "--seed-level3-demo" in text
    assert "8080:8080" in text


def test_server_entrypoint_serves_seeded_ui_and_api(tmp_path):
    port = _free_port()
    db_path = tmp_path / "vad.sqlite3"
    ui_root = tmp_path / "ui"
    evidence_root = tmp_path / "evidence"
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "vad.server.serve",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--db",
            str(db_path),
            "--ui-root",
            str(ui_root),
            "--evidence-root",
            str(evidence_root),
            "--seed-demo",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        base_url = f"http://127.0.0.1:{port}"
        _wait_for_health(base_url, process)
        with urlopen(f"{base_url}/") as response:
            assert 'id="dashboard-panel"' in response.read().decode("utf-8")
        with urlopen(f"{base_url}/runs") as response:
            assert "demo-run" in response.read().decode("utf-8")
        with urlopen(f"{base_url}/dashboard") as response:
            body = response.read().decode("utf-8")
            assert "Claude Code" in body
            assert "Antigravity" in body
        with urlopen(f"{base_url}/app.js") as response:
            assert "loadDashboard" in response.read().decode("utf-8")
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def test_shared_ui_server_helper_serves_seeded_ui_and_api(tmp_path):
    server = prepare_ui_server(
        "127.0.0.1",
        0,
        tmp_path / "evidence",
        tmp_path / "vad.sqlite3",
        tmp_path / "ui",
        seed_demo=True,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address[:2]
        base_url = f"http://{host}:{port}"
        with urlopen(f"{base_url}/health") as response:
            assert response.status == 200
        with urlopen(f"{base_url}/") as response:
            assert 'id="dashboard-panel"' in response.read().decode("utf-8")
        with urlopen(f"{base_url}/runs") as response:
            assert "demo-run" in response.read().decode("utf-8")
        with urlopen(f"{base_url}/dashboard") as response:
            body = response.read().decode("utf-8")
            assert "Codex" in body
            assert "VSCode" in body
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_level3_demo_server_serves_end_to_end_ui_api_data(tmp_path):
    server = prepare_ui_server(
        "127.0.0.1",
        0,
        tmp_path / "evidence",
        tmp_path / "vad.sqlite3",
        tmp_path / "ui",
        seed_level3_demo=True,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address[:2]
        base_url = f"http://{host}:{port}"
        with urlopen(f"{base_url}/health") as response:
            assert response.status == 200
        with urlopen(f"{base_url}/") as response:
            assert 'id="dashboard-panel"' in response.read().decode("utf-8")
        with urlopen(f"{base_url}/runs") as response:
            runs_payload = json.loads(response.read())
        run_ids = {run["run_id"] for run in runs_payload["runs"]}
        assert {"level3-demo-success", "level3-demo-failure"}.issubset(run_ids)

        with urlopen(f"{base_url}/runs/level3-demo-success/evidence") as response:
            evidence_payload = json.loads(response.read())
        assert evidence_payload["evidence"]["final_decision"] == "passed"

        with urlopen(f"{base_url}/dashboard") as response:
            dashboard = json.loads(response.read())
        with urlopen(f"{base_url}/dashboard/replay?run_id=level3-demo-success") as response:
            replay = json.loads(response.read())
        assert "Rollback triggered" in json.dumps(dashboard)
        assert "level3-demo-failure" in {run["run_id"] for run in dashboard["runs"]}
        assert replay["replay"]["run_id"] == "level3-demo-success"
        assert {run["run_id"] for run in replay["runs"]} == {"level3-demo-success"}
        assert {event["run_id"] for event in replay["event_timeline"]} == {"level3-demo-success"}
        assert any(activity["kind"] == "work_item" for activity in dashboard["activity"])
        assert set(dashboard["task_board_columns"]) == {"active", "blocked", "passed", "failed", "needs_human"}
        assert "proof_status" in dashboard
        assert "terminal_status" in dashboard
        assert {plugin["publication_readiness"] for plugin in dashboard["plugin_status"]} >= {"local_ready", "blocked"}
        assert all(plugin["local_version"] == plugin["version"] for plugin in dashboard["plugin_status"])
        assert dashboard["event_timeline"]
        assert any(event["status"] in {"blocked", "failed"} for event in dashboard["event_timeline"])
        for client in ["Claude Code", "VSCode", "Codex", "Antigravity", "Cursor", "Windsurf", "OpenCode", "Generic MCP/A2A"]:
            assert dashboard["client_counts"][client] >= 1

        request = Request(
            f"{base_url}/actions/approve",
            data=json.dumps({
                "run_id": "level3-demo-success",
                "actor": "claude-code-builder",
                "actor_role": "release_guardian",
                "action": "approve_release",
            }).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            urlopen(request)
        except HTTPError as response:
            assert response.code == 403
            denied_payload = json.loads(response.read())
        else:
            raise AssertionError("self approval unexpectedly succeeded")
        assert denied_payload["approval"]["decision"]["allow"] is False
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_dashboard_end_to_end_smoke_covers_live_views_replay_and_denied_action(tmp_path):
    server = prepare_ui_server(
        "127.0.0.1",
        0,
        tmp_path / "evidence",
        tmp_path / "vad.sqlite3",
        tmp_path / "ui",
        seed_multi_client_simulator=True,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address[:2]
        base_url = f"http://{host}:{port}"
        with urlopen(f"{base_url}/health") as response:
            assert response.status == 200
        with urlopen(f"{base_url}/ready") as response:
            assert json.loads(response.read()) == {"status": "ready"}
        with urlopen(f"{base_url}/dashboard") as response:
            dashboard = json.loads(response.read())
        with urlopen(f"{base_url}/dashboard/replay?run_id=multi-client-simulator") as response:
            replay = json.loads(response.read())

        expected_clients = {"Codex", "Antigravity", "Claude Code", "VS Code", "Windsurf", "Cursor", "OpenCode", "Generic MCP/A2A"}
        assert {client["display_name"] for client in dashboard["active_clients"]} == expected_clients
        assert set(dashboard["client_counts"]) == expected_clients
        assert set(dashboard["task_board_columns"]) == {"active", "blocked", "passed", "failed", "needs_human"}
        assert any(item["task_id"].startswith("sim-task-") for item in dashboard["task_board_columns"]["passed"])
        assert replay["replay"]["run_id"] == "multi-client-simulator"
        assert {event["run_id"] for event in replay["event_timeline"]} == {"multi-client-simulator"}
        assert replay["event_timeline"]
        assert {event["kind"] for event in dashboard["event_timeline"]} >= {
            "heartbeat",
            "tool_call_started",
            "tool_call_finished",
            "work_item",
            "proof_started",
            "proof_finished",
            "signer_event",
            "deployment_event",
        }

        request = Request(
            f"{base_url}/actions/approve",
            data=json.dumps({
                "run_id": "multi-client-simulator",
                "actor": "multi-client-builder",
                "actor_role": "release_guardian",
                "action": "approve_release",
            }).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            urlopen(request)
        except HTTPError as response:
            assert response.code == 403
            denied_payload = json.loads(response.read())
        else:
            raise AssertionError("self approval unexpectedly succeeded")
        assert denied_payload["approval"]["decision"]["allow"] is False
        assert denied_payload["approval"]["decision"]["denials"] == ["builder may not approve own run"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_demo_seed_data_is_ingested_as_control_plane_events(tmp_path):
    store = seed_demo_store(tmp_path / "vad.sqlite3")

    assert store.list_dashboard_activity() == []
    events = store.list_control_plane_events()
    assert {event.client_id for event in events} >= {"Claude Code", "VSCode", "Codex", "Antigravity"}
    assert {event.kind.value for event in events} >= {"swarm", "provider", "signing", "deployment"}
    assert store.control_plane_dashboard_snapshot()["client_counts"]["Codex"] == 1


def test_level3_demo_seed_data_replays_from_control_plane_events(tmp_path):
    store = seed_level3_demo_store(tmp_path / "vad.sqlite3")

    events = store.list_control_plane_events()
    assert any(event.kind.value == "work_item" for event in events)
    assert any("Rollback triggered" in event.summary for event in events)
    dashboard = store.control_plane_dashboard_snapshot()
    assert "level3-demo-failure" in {run["run_id"] for run in dashboard["runs"]}
    assert dashboard["client_counts"]["Generic MCP/A2A"] >= 1
    assert dashboard["event_timeline"]


def test_multi_client_simulator_fixture_emits_all_clients_and_event_types(tmp_path):
    store = seed_multi_client_simulator_store(tmp_path / "vad.sqlite3")

    manifests = store.list_client_manifests()
    client_statuses = store.list_client_statuses()
    events = store.list_control_plane_events(run_id="multi-client-simulator")
    dashboard = store.control_plane_dashboard_snapshot(run_id="multi-client-simulator")
    expected_clients = {
        "Codex",
        "Antigravity",
        "Claude Code",
        "VS Code",
        "Windsurf",
        "Cursor",
        "OpenCode",
        "Generic MCP/A2A",
    }

    assert {manifest.display_name for manifest in manifests} == expected_clients
    assert {status.manifest.display_name for status in client_statuses} == expected_clients
    assert {status.status.value for status in client_statuses} == {"active"}
    assert {event.client_label for event in events} == expected_clients
    assert set(dashboard["client_counts"]) == expected_clients
    assert {event.kind.value for event in events} >= {
        "heartbeat",
        "tool_call_started",
        "tool_call_finished",
        "work_item",
        "proof_started",
        "proof_finished",
        "signer_event",
        "deployment_event",
    }
    for client in expected_clients:
        client_events = [event for event in store.list_control_plane_events() if event.client_label == client]
        assert any(event.kind.value == "message" and "registered" in event.summary for event in client_events)
        assert {event.kind.value for event in client_events} >= {
            "heartbeat",
            "tool_call_started",
            "tool_call_finished",
            "work_item",
            "proof_started",
            "proof_finished",
            "signer_event",
            "deployment_event",
        }


def test_multi_client_role_separation_scenario_denies_self_approval_and_recovers_stale_builder(tmp_path):
    store = seed_multi_client_role_separation_store(tmp_path / "vad.sqlite3")

    events = store.list_control_plane_events(run_id="multi-client-role-separation")
    approvals = store.list_approval_events("multi-client-role-separation")
    leases = store.list_task_leases()
    statuses = {snapshot.manifest.display_name: snapshot for snapshot in store.list_client_statuses()}
    role_clients = {
        event.role: event.client_label
        for event in events
        if event.role in {"planner", "builder", "verifier", "auditor", "release_guardian"}
    }

    assert role_clients["planner"] == "Antigravity"
    assert role_clients["builder"] == "Codex"
    assert role_clients["verifier"] == "VS Code"
    assert role_clients["auditor"] == "Windsurf"
    assert role_clients["release_guardian"] == "OpenCode"
    assert approvals[0].actor == "codex-builder"
    assert approvals[0].decision.allow is False
    assert approvals[0].decision.denials == ["builder may not approve own run"]
    assert approvals[1].actor == "opencode-release-guardian"
    assert approvals[1].decision.allow is True
    assert statuses["Codex"].status.value == "stale"
    assert statuses["Codex"].lost_task_leases == ("role-build",)
    assert [lease for lease in leases if lease.task_id == "role-build"][0].status.value == "expired"
    assert any(event.kind.value == "recovery_action" and event.status.value == "needs_human" for event in events)
    assert any(event.kind.value == "heartbeat" and event.status.value == "stale" for event in events)


def test_legacy_dashboard_activity_path_remains_compatible(tmp_path):
    store = ServerStore(tmp_path / "vad.sqlite3")
    assert store.has_control_plane_events() is False


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _wait_for_health(base_url: str, process: subprocess.Popen, timeout: float = 10.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if process.poll() is not None:
            stdout, stderr = process.communicate()
            raise AssertionError(f"server exited early\nstdout={stdout}\nstderr={stderr}")
        try:
            with urlopen(f"{base_url}/health", timeout=0.5) as response:
                if response.status == 200:
                    return
        except OSError:
            time.sleep(0.1)
    raise AssertionError("server did not become healthy")
