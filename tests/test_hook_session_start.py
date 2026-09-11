import json
import pytest
from vad.plugins.claude_code.hooks.session_start import SessionStartHook, SessionStartResult
from vad.memory.skill_ledger import SkillLedger, SkillLedgerEntry, SkillOutcome
from vad.memory.ledger_retriever import LedgerRetriever


def test_session_start_hook_default(tmp_path):
    ledger_file = tmp_path / "ledger.jsonl"
    ledger = SkillLedger(ledger_path=str(ledger_file))
    ledger.record(SkillLedgerEntry(
        session_id="sess-prev",
        skill_name="compile_proto",
        agent_id="claude",
        outcome=SkillOutcome.FAILURE,
        error_snippet="Missing protoc include path",
        repo="voice-agentic",
    ))
    
    retriever = LedgerRetriever(ledger=ledger)
    hook = SessionStartHook(retriever=retriever, tracker_path="nonexistent.md")

    payload = {"session_id": "sess-100", "repo": "voice-agentic", "eip_name": "feat-vad"}
    res = hook.process(payload)

    assert isinstance(res, SessionStartResult)
    assert res.session_id == "sess-100"
    assert res.has_active_eip is True
    assert "Missing protoc include path" in res.injected_context
    assert "Active Repository: `voice-agentic`" in res.injected_context
