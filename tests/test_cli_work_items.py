import json
import sys

from vad.server.db.store import ServerStore


def run_cli(monkeypatch, argv, capsys):
    from vad import cli

    monkeypatch.setattr(sys, "argv", argv)
    cli.main()
    return json.loads(capsys.readouterr().out)


def register_active_client(monkeypatch, capsys, db_path, tmp_path):
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
        "repo_patch",
        "--workspace-root",
        str(tmp_path),
        "--trust-state",
        "trusted",
    ], capsys)
    run_cli(monkeypatch, [
        "vad",
        "clients",
        "heartbeat",
        "--db",
        str(db_path),
        "codex-local",
        "--actor",
        "codex",
        "--role",
        "builder",
    ], capsys)


def test_cli_work_items_create_list_show_and_scheduler_assign(monkeypatch, capsys, tmp_path):
    db_path = tmp_path / "vad.sqlite3"
    register_active_client(monkeypatch, capsys, db_path, tmp_path)

    created = run_cli(monkeypatch, [
        "vad",
        "work-items",
        "create",
        "--db",
        str(db_path),
        "--work-item-id",
        "work-cli",
        "--run-id",
        "run-cli",
        "--title",
        "CLI work",
        "--description",
        "Exercise CLI work item commands.",
        "--work-role",
        "builder",
        "--requested-capability",
        "repo_patch",
        "--priority",
        "5",
        "--effort-type",
        "feature",
        "--mees-estimate",
        "92",
        "--token-budget",
        "2500",
        "--approval-required",
        "--high-risk",
        "--operator-intent-ref",
        "operator-intent-cli",
        "--approval-ref",
        "approval-cli",
    ], capsys)
    listed = run_cli(monkeypatch, [
        "vad",
        "work-items",
        "list",
        "--db",
        str(db_path),
        "--run-id",
        "run-cli",
        "--status",
        "queued",
    ], capsys)
    shown = run_cli(monkeypatch, [
        "vad",
        "work-items",
        "show",
        "--db",
        str(db_path),
        "work-cli",
    ], capsys)
    assigned = run_cli(monkeypatch, [
        "vad",
        "work-items",
        "assign",
        "--db",
        str(db_path),
        "work-cli",
    ], capsys)

    assert created["work_item"]["status"] == "queued"
    assert created["work_item"]["governance"]["effort_type"] == "feature"
    assert created["work_item"]["governance"]["mees_estimate"] == 92
    assert created["work_item"]["governance"]["token_budget"] == 2500
    assert created["event"]["kind"] == "work_item"
    assert [item["work_item_id"] for item in listed["work_items"]] == ["work-cli"]
    assert shown["work_item"]["title"] == "CLI work"
    assert shown["work_item"]["governance"]["operator_intent_ref"] == "operator-intent-cli"
    assert assigned["selected_client_id"] == "codex-local"
    assert assigned["work_item"]["status"] == "assigned"
    assert ServerStore(db_path).load_work_item("work-cli").assigned_client_id == "codex-local"


def test_cli_work_items_transitions_and_denial_exit_nonzero(monkeypatch, capsys, tmp_path):
    db_path = tmp_path / "vad.sqlite3"
    run_cli(monkeypatch, [
        "vad",
        "work-items",
        "create",
        "--db",
        str(db_path),
        "--work-item-id",
        "work-transition",
        "--run-id",
        "run-cli",
        "--title",
        "Transition",
        "--work-role",
        "builder",
    ], capsys)

    blocked = run_cli(monkeypatch, [
        "vad",
        "work-items",
        "block",
        "--db",
        str(db_path),
        "work-transition",
        "--reason",
        "needs human review",
    ], capsys)
    requeued = run_cli(monkeypatch, [
        "vad",
        "work-items",
        "requeue",
        "--db",
        str(db_path),
        "work-transition",
    ], capsys)
    cancelled = run_cli(monkeypatch, [
        "vad",
        "work-items",
        "cancel",
        "--db",
        str(db_path),
        "work-transition",
    ], capsys)

    try:
        run_cli(monkeypatch, [
            "vad",
            "work-items",
            "complete",
            "--db",
            str(db_path),
            "work-transition",
        ], capsys)
    except SystemExit as exc:
        assert exc.code == 1
    else:
        raise AssertionError("invalid terminal transition unexpectedly exited zero")

    denied = json.loads(capsys.readouterr().out)
    assert blocked["work_item"]["status"] == "blocked"
    assert blocked["work_item"]["blocked_reason"] == "needs human review"
    assert requeued["work_item"]["status"] == "requeued"
    assert cancelled["work_item"]["status"] == "cancelled"
    assert denied["event"]["kind"] == "policy_denied"
    assert denied["decision"]["allow"] is False


def test_cli_work_items_missing_item_exits_nonzero(monkeypatch, capsys, tmp_path):
    db_path = tmp_path / "vad.sqlite3"

    try:
        run_cli(monkeypatch, [
            "vad",
            "work-items",
            "show",
            "--db",
            str(db_path),
            "missing-work",
        ], capsys)
    except SystemExit as exc:
        assert exc.code == 1
    else:
        raise AssertionError("missing work item unexpectedly exited zero")

    assert json.loads(capsys.readouterr().out) == {
        "error": "work_item_not_found",
        "work_item_id": "missing-work",
    }


def test_cli_work_items_reject_high_risk_governance_without_approval(monkeypatch, capsys, tmp_path):
    db_path = tmp_path / "vad.sqlite3"

    try:
        run_cli(monkeypatch, [
            "vad",
            "work-items",
            "create",
            "--db",
            str(db_path),
            "--work-item-id",
            "work-risk",
            "--run-id",
            "run-cli",
            "--title",
            "Risky CLI work",
            "--work-role",
            "builder",
            "--effort-type",
            "deploy",
            "--mees-estimate",
            "80",
            "--token-budget",
            "1000",
            "--high-risk",
        ], capsys)
    except SystemExit as exc:
        assert exc.code == 1
    else:
        raise AssertionError("invalid high-risk governance unexpectedly succeeded")

    payload = json.loads(capsys.readouterr().out)
    assert payload["error"] == "invalid_work_item"
    assert "high-risk work requires work-item approval" in payload["detail"]
