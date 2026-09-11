from pathlib import Path
import pytest
from vad.audit.local_audit import AuditEvent, LocalAuditLogger


def test_local_audit_dual_write_and_query(tmp_path):
    db_file = tmp_path / "test_audit.db"
    jsonl_file = tmp_path / "test_audit.jsonl"

    logger = LocalAuditLogger(sqlite_path=str(db_file), jsonl_path=str(jsonl_file))

    event = AuditEvent(
        session_id="sess-100",
        agent_id="claude-agent",
        action="PreToolUse",
        tool="pytest",
        outcome="allow",
        eip_id="eip-voice-auth",
        metadata={"cost": 0.005},
    )
    logger.log(event)

    # 1. Verify JSONL exists and contains serialized event
    assert jsonl_file.exists()
    lines = jsonl_file.read_text().strip().split("\n")
    assert len(lines) == 1
    assert "sess-100" in lines[0]

    # 2. Verify SQLite query
    assert logger.count_events() == 1
    queried = logger.query_events(session_id="sess-100")
    assert len(queried) == 1
    assert queried[0].agent_id == "claude-agent"
    assert queried[0].tool == "pytest"
    assert queried[0].metadata["cost"] == 0.005
