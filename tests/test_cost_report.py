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
