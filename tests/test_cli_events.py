import json
import sys

from vad.server.db.store import ServerStore


def run_cli(monkeypatch, argv, capsys):
    from vad import cli

    monkeypatch.setattr(sys, "argv", argv)
    cli.main()
    return json.loads(capsys.readouterr().out)


def test_cli_events_emit_persists_control_plane_event(monkeypatch, capsys, tmp_path):
    db_path = tmp_path / "vad.sqlite3"

    emitted = run_cli(monkeypatch, [
        "vad",
        "events",
        "emit",
        "--db",
        str(db_path),
        "--event-id",
        "event-1",
        "--sequence",
        "1",
        "--client-id",
        "codex-local",
        "--run-id",
        "run-1",
        "--task-id",
        "build",
        "--kind",
        "tool_call_started",
        "--status",
        "active",
        "--actor",
        "codex",
        "--role",
        "builder",
        "--summary",
        "Codex started a tool call.",
    ], capsys)

    events = ServerStore(db_path).list_control_plane_events()

    assert emitted["decision"]["allow"] is True
    assert emitted["event"]["event_id"] == "event-1"
    assert events[0].event_id == "event-1"


def test_cli_events_emit_exits_nonzero_for_policy_denial(monkeypatch, capsys, tmp_path):
    db_path = tmp_path / "vad.sqlite3"

    try:
        run_cli(monkeypatch, [
            "vad",
            "events",
            "emit",
            "--db",
            str(db_path),
            "--event-id",
            "deploy-1",
            "--sequence",
            "1",
            "--client-id",
            "codex-local",
            "--kind",
            "deployment_event",
            "--status",
            "active",
            "--actor",
            "codex",
            "--role",
            "builder",
            "--summary",
            "Attempt deployment.",
        ], capsys)
    except SystemExit as exc:
        assert exc.code == 1
    else:
        raise AssertionError("policy denied event unexpectedly exited zero")

    payload = json.loads(capsys.readouterr().out)
    assert payload["event"]["kind"] == "policy_denied"
    assert payload["decision"]["allow"] is False
