import pytest
from vad.memory.gaps_miner import GapsMiner
from vad.memory.skill_ledger import SkillLedger, SkillLedgerEntry, SkillOutcome


def test_gaps_miner_surfaces_high_failure_skills(tmp_path):
    ledger_file = tmp_path / "gaps_ledger.jsonl"
    ledger = SkillLedger(str(ledger_file))

    # Skill 1: 3 runs, 2 failures (66% failure)
    for i in range(2):
        ledger.record(
            SkillLedgerEntry(
                session_id=f"fail-{i}",
                skill_name="webrtc-handshake",
                agent_id="media-agent",
                outcome=SkillOutcome.FAILURE,
                error_snippet="ICE connection state failed during stun negotiation",
            )
        )
    ledger.record(
        SkillLedgerEntry(
            session_id="pass-1",
            skill_name="webrtc-handshake",
            agent_id="media-agent",
            outcome=SkillOutcome.SUCCESS,
        )
    )

    # Skill 2: 2 runs, 0 failures (0% failure)
    for i in range(2):
        ledger.record(
            SkillLedgerEntry(
                session_id=f"pass-s2-{i}",
                skill_name="health-check",
                agent_id="ops-agent",
                outcome=SkillOutcome.SUCCESS,
            )
        )

    miner = GapsMiner(ledger=ledger, failure_rate_threshold=0.3, min_samples=2)
    report = miner.mine_gaps()

    assert report.total_entries_analyzed == 5
    assert len(report.gaps) == 1
    assert report.gaps[0].skill_name == "webrtc-handshake"
    assert report.gaps[0].failure_rate == 0.6667
    assert "webrtc-handshake" in report.priority_skills_to_improve
    assert any("Refine instructions" in r for r in report.gaps[0].recommendations)
