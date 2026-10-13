import pytest
from vad.audit.cost_report import CostReportGenerator
from vad.audit.local_audit import LocalAuditLogger


def test_cost_report_generation(tmp_path):
    gen = CostReportGenerator(price_per_1k_tokens=0.003, budget_usd_ceiling=1.0)
    report = gen.generate_report(
        session_id="sess-xyz",
        agent_id="claude-code",
        tokens=5000,
        tool_calls=["file_read", "pytest", "file_read"],
        mees_delta=+2.5,
    )

    assert report.total_tokens == 5000
    assert report.estimated_usd == 0.015
    assert report.tool_call_counts["file_read"] == 2
    assert report.tool_call_counts["pytest"] == 1
    assert report.is_over_budget is False
    assert "sess-xyz" in gen.format_summary(report)


def test_cost_report_logs_to_audit(tmp_path):
    db_file = tmp_path / "cost_audit.db"
    jsonl_file = tmp_path / "cost_audit.jsonl"
    logger = LocalAuditLogger(sqlite_path=str(db_file), jsonl_path=str(jsonl_file))

    gen = CostReportGenerator(budget_usd_ceiling=0.01)  # Low ceiling
    report = gen.generate_report(
        session_id="sess-high-cost",
        agent_id="gpt-4o",
        tokens=10000,
        tool_calls=["terminal_run"],
    )
    assert report.is_over_budget is True

    event = gen.log_to_audit(report, logger)
    assert event.action == "CostReport"
    assert event.outcome == "alert"

    queried = logger.query_events(session_id="sess-high-cost")
    assert len(queried) == 1
    assert queried[0].metadata["is_over_budget"] is True


def test_exact_turn_cost_reporting():
    from vad.audit.cost_report import TranscriptTurnUsage

    gen = CostReportGenerator(budget_usd_ceiling=2.0)
    turns = [
        TranscriptTurnUsage(
            model="claude-3-5-sonnet",
            prompt_tokens=10000,  # 10k * $3/M = $0.03
            completion_tokens=2000,  # 2k * $15/M = $0.03
            cache_read_tokens=50000,  # 50k * $0.30/M = $0.015
        ),
        TranscriptTurnUsage(
            model="gpt-4o",
            prompt_tokens=20000,  # 20k * $2.50/M = $0.05
            completion_tokens=1000,  # 1k * $10/M = $0.01
        ),
    ]
    report = gen.generate_report_from_turns(
        session_id="sess-exact",
        agent_id="multi-agent",
        turns=turns,
        tool_calls=["git_diff", "pytest"],
    )

    assert report.prompt_tokens == 30000
    assert report.completion_tokens == 3000
    assert report.cache_tokens == 50000
    assert report.total_tokens == 83000
    # Expected cost ~ 0.03 + 0.03 + 0.015 + 0.05 + 0.01 = 0.135
    assert 0.13 <= report.estimated_usd <= 0.14
    assert report.tool_call_counts["git_diff"] == 1
    assert report.is_over_budget is False
    assert "claude-3-5-sonnet" in report.model_breakdown
    assert "gpt-4o" in report.model_breakdown

