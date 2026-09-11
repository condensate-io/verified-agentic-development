import pytest
from vad.memory.ledger_retriever import LedgerRetriever
from vad.memory.skill_ledger import SkillLedger, SkillLedgerEntry, SkillOutcome


def test_ledger_retriever_formats_session_context(tmp_path):
    ledger_file = tmp_path / "test_ledger.jsonl"
    ledger = SkillLedger(str(ledger_file))

    # Add failure entries for a repo
    ledger.record(
        SkillLedgerEntry(
            session_id="s1",
            skill_name="rtp-streamer",
            agent_id="audio-agent",
            outcome=SkillOutcome.FAILURE,
            error_snippet="Missing libopus codec dependency",
            repo="voice-agentic",
        )
    )
    ledger.record(
        SkillLedgerEntry(
            session_id="s2",
            skill_name="rtp-streamer",
            agent_id="audio-agent",
            outcome=SkillOutcome.SUCCESS,
            repo="voice-agentic",
        )
    )

    retriever = LedgerRetriever(ledger=ledger)
    context_str = retriever.format_for_session_start(repo="voice-agentic")

    assert "Historical Skill Execution Memory" in context_str
    assert "rtp-streamer" in context_str
    assert "Missing libopus codec dependency" in context_str
    assert "(1/2 failures)" in context_str


def test_ledger_retriever_empty_when_no_failures(tmp_path):
    ledger_file = tmp_path / "empty_ledger.jsonl"
    ledger = SkillLedger(str(ledger_file))
    ledger.record(
        SkillLedgerEntry(
            session_id="s1",
            skill_name="clean-skill",
            agent_id="agent-1",
            outcome=SkillOutcome.SUCCESS,
            repo="clean-repo",
        )
    )

    retriever = LedgerRetriever(ledger=ledger)
    context_str = retriever.format_for_session_start(repo="clean-repo")
    assert context_str == ""
