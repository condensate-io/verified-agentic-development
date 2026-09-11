import pytest
from vad.audit.local_audit import LocalAuditLogger
from vad.memory.skill_ledger import SkillLedger, SkillOutcome
from vad.plugins.claude_code.hooks.post_tool_use import PostToolUseHook


def test_post_tool_use_audit_and_ledger_recording(tmp_path):
    audit_db = tmp_path / "test_audit.db"
    audit_jsonl = tmp_path / "test_audit.jsonl"
    ledger_jsonl = tmp_path / "test_ledger.jsonl"

    audit_logger = LocalAuditLogger(sqlite_path=str(audit_db), jsonl_path=str(audit_jsonl))
    skill_ledger = SkillLedger(str(ledger_jsonl))

    hook = PostToolUseHook(audit_logger=audit_logger, skill_ledger=skill_ledger)

    payload = {
        "session_id": "sess-post-1",
        "agent_id": "claude-code",
        "tool_name": "pytest",
        "tool_input": {"command": "pytest tests/test_smoke.py"},
        "tool_output": "3 passed in 1.2s",
        "exit_code": 0,
        "duration_ms": 1200.0,
        "repo": "voice-core",
    }

    res = hook.process(payload)
    assert res["status"] == "recorded"

    # Verify audit event was logged
    events = audit_logger.query_events(session_id="sess-post-1")
    assert len(events) == 1
    assert events[0].action == "PostToolUse"
    assert events[0].outcome == "success"

    # Verify skill ledger was recorded
    ledger_entries = skill_ledger.get_entries(skill_name="pytest")
    assert len(ledger_entries) == 1
    assert ledger_entries[0].outcome == SkillOutcome.SUCCESS
    assert ledger_entries[0].duration_ms == 1200.0


def test_post_tool_use_failure_capture(tmp_path):
    audit_db = tmp_path / "test_audit.db"
    audit_jsonl = tmp_path / "test_audit.jsonl"
    ledger_jsonl = tmp_path / "test_ledger.jsonl"

    audit_logger = LocalAuditLogger(sqlite_path=str(audit_db), jsonl_path=str(audit_jsonl))
    skill_ledger = SkillLedger(str(ledger_jsonl))
    hook = PostToolUseHook(audit_logger=audit_logger, skill_ledger=skill_ledger)

    payload = {
        "session_id": "sess-fail-1",
        "agent_id": "claude-code",
        "tool_name": "bash",
        "tool_input": {"command": "invalid_command"},
        "tool_output": "command not found",
        "exit_code": 127,
        "duration_ms": 50.0,
    }

    hook.process(payload)

    entries = skill_ledger.get_entries(outcome=SkillOutcome.FAILURE)
    assert len(entries) == 1
    assert "command not found" in entries[0].error_snippet
