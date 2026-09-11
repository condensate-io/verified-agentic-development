import json
from pathlib import Path
import pytest
from vad.plugins.claude_code.hooks.subagent_stop import SubagentStopHook


def test_subagent_stop_records_evidence(tmp_path):
    ev_dir = tmp_path / "evidence"
    hook = SubagentStopHook(evidence_dir=str(ev_dir))

    payload = {
        "subagent_id": "subagent-auth-1",
        "task_id": "TASK-AUTH",
        "status": "success",
        "evidence": {"tests_passed": 5, "coverage": 91.2},
    }

    res = hook.process(payload)
    assert res.allowed is True
    assert res.evidence_recorded is True

    target_file = ev_dir / "TASK-AUTH.json"
    assert target_file.exists()
    data = json.loads(target_file.read_text())
    assert data["evidence"]["coverage"] == 91.2


def test_subagent_stop_blocks_when_evidence_missing(tmp_path):
    hook = SubagentStopHook(evidence_dir=str(tmp_path / "ev"))
    payload = {
        "subagent_id": "subagent-rogue",
        "task_id": "TASK-ROGUE",
        "status": "success",
        "evidence": None,  # Missing!
    }

    res = hook.process(payload)
    assert res.allowed is False
    assert "produced no evidence artifact" in res.reason
