import json
import sys


def run_cli(monkeypatch, argv, capsys):
    from vad import cli

    monkeypatch.setattr(sys, "argv", argv)
    cli.main()
    return json.loads(capsys.readouterr().out)


def test_cli_clients_register_list_and_unregister(monkeypatch, capsys, tmp_path):
    db_path = tmp_path / "vad.sqlite3"

    registered = run_cli(monkeypatch, [
        "vad",
        "clients",
        "register",
        "--db",
        str(db_path),
        "--client-id",
        "codex-local",
        "--display-name",
        "Codex",
        "--client-type",
        "codex",
        "--version",
        "1.0.0",
        "--connection-mode",
        "mcp",
        "--capability",
        "repo_read",
        "--capability",
        "tool_call",
        "--workspace-root",
        str(tmp_path),
        "--trust-state",
        "trusted",
    ], capsys)
    listed = run_cli(monkeypatch, ["vad", "clients", "list", "--db", str(db_path)], capsys)
    removed = run_cli(monkeypatch, ["vad", "clients", "unregister", "--db", str(db_path), "codex-local"], capsys)

    assert registered["client"]["client_id"] == "codex-local"
    assert registered["event"]["actor"] == "codex-local"
    assert listed["clients"][0]["manifest"]["supported_capabilities"] == ["repo_read", "tool_call"]
    assert listed["clients"][0]["status"] == "disconnected"
    assert removed["client"]["display_name"] == "Codex"
    assert removed["event"]["summary"] == "Client Codex unregistered."


def test_cli_clients_heartbeat_and_mark_stale(monkeypatch, capsys, tmp_path):
    from vad.control_plane.work_items import WorkItem, WorkItemStatus
    from vad.server.db.store import ServerStore

    db_path = tmp_path / "vad.sqlite3"
    run_cli(monkeypatch, [
        "vad",
        "clients",
        "register",
        "--db",
        str(db_path),
        "--client-id",
        "codex-local",
        "--display-name",
        "Codex",
        "--client-type",
        "codex",
        "--version",
        "1.0.0",
        "--connection-mode",
        "mcp",
        "--capability",
        "repo_read",
        "--workspace-root",
        str(tmp_path),
    ], capsys)

    heartbeat = run_cli(monkeypatch, [
        "vad",
        "clients",
        "heartbeat",
        "--db",
        str(db_path),
        "codex-local",
        "--run-id",
        "run-1",
        "--task-id",
        "build",
        "--actor",
        "codex",
        "--role",
        "builder",
    ], capsys)
    ServerStore(db_path).save_work_item(WorkItem(
        work_item_id="cli-work",
        run_id="run-1",
        title="CLI stale recovery",
        role="builder",
        status=WorkItemStatus.ASSIGNED,
        assigned_client_id="codex-local",
    ))
    stale = run_cli(monkeypatch, [
        "vad",
        "clients",
        "mark-stale",
        "--db",
        str(db_path),
        "--stale-after-seconds",
        "-1",
    ], capsys)

    assert heartbeat["client"]["status"] == "active"
    assert heartbeat["event"]["kind"] == "heartbeat"
    assert stale["stale_clients"][0]["status"] == "stale"
    assert stale["recovered_work_items"][0]["work_item"]["work_item_id"] == "cli-work"
    assert stale["recovered_work_items"][0]["work_item"]["status"] == "requeued"
    assert stale["recovered_work_items"][0]["work_item"]["assigned_client_id"] is None
    assert stale["recovery_events"][0]["kind"] == "recovery_action"


def test_cli_clients_duplicate_register_exits_nonzero(monkeypatch, capsys, tmp_path):
    db_path = tmp_path / "vad.sqlite3"
    argv = [
        "vad",
        "clients",
        "register",
        "--db",
        str(db_path),
        "--client-id",
        "codex-local",
        "--display-name",
        "Codex",
        "--client-type",
        "codex",
        "--version",
        "1.0.0",
        "--connection-mode",
        "mcp",
        "--capability",
        "repo_read",
        "--workspace-root",
        str(tmp_path),
    ]
    run_cli(monkeypatch, argv, capsys)

    try:
        run_cli(monkeypatch, argv, capsys)
    except SystemExit as exc:
        assert exc.code == 1
    else:
        raise AssertionError("duplicate registration unexpectedly succeeded")

    assert json.loads(capsys.readouterr().out) == {"error": "duplicate_client", "client_id": "codex-local"}
