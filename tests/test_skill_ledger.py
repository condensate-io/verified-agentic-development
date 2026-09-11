import pytest
from vad.memory.skill_ledger import SkillLedger, SkillLedgerEntry, SkillOutcome


def test_skill_ledger_recording_and_query(tmp_path):
    ledger_path = tmp_path / "skill_ledger.jsonl"
    ledger = SkillLedger(str(ledger_path))

    # Record success
    ledger.record(
        SkillLedgerEntry(
            session_id="s1",
            skill_name="sip-troubleshooter",
            agent_id="claude-1",
            outcome=SkillOutcome.SUCCESS,
            duration_ms=450.0,
            repo="voice-agentic",
        )
    )

    # Record failure
    ledger.record(
        SkillLedgerEntry(
            session_id="s2",
            skill_name="sip-troubleshooter",
            agent_id="claude-1",
            outcome=SkillOutcome.FAILURE,
            error_snippet="SIP 408 Request Timeout",
            duration_ms=820.0,
            repo="voice-agentic",
        )
    )

    # Verify queries
    entries = ledger.get_entries(skill_name="sip-troubleshooter")
    assert len(entries) == 2

    failures = ledger.get_entries(skill_name="sip-troubleshooter", outcome=SkillOutcome.FAILURE)
    assert len(failures) == 1
    assert "SIP 408" in failures[0].error_snippet

    # Verify failure rate calculation (1/2 = 50%)
    rate = ledger.get_failure_rate("sip-troubleshooter")
    assert rate == 0.5
