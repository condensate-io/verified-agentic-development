import pytest
from textual.app import App, ComposeResult
from textual.widgets import DataTable, Static
from vad.audit.local_audit import LocalAuditLogger, AuditEvent
from vad.ui.tui.analytics_panel import AnalyticsCalculator, AnalyticsPanel


def test_analytics_calculator_empty(tmp_path):
    calc = AnalyticsCalculator(sqlite_path=str(tmp_path / "empty.db"))
    res = calc.compute_metrics()
    assert res["total_events"] == 0
    assert res["success_rate"] == 0.0


def test_analytics_calculator_with_events(tmp_path):
    db_file = tmp_path / "audit.db"
    logger = LocalAuditLogger(sqlite_path=str(db_file), jsonl_path=str(tmp_path / "audit.jsonl"))

    logger.log(AuditEvent(
        session_id="s1",
        agent_id="claude-1",
        action="edit_file",
        outcome="success",
        metadata={"tokens": 1200, "cost_usd": 0.0036},
    ))
    logger.log(AuditEvent(
        session_id="s1",
        agent_id="copilot-1",
        action="review_diff",
        outcome="failure",
        metadata={"tokens": 800, "cost_usd": 0.0024},
    ))

    calc = AnalyticsCalculator(sqlite_path=str(db_file))
    res = calc.compute_metrics()

    assert res["total_events"] == 2
    assert res["success_rate"] == 50.0
    assert res["total_tokens"] == 2000
    assert res["estimated_cost_usd"] == 0.006
    assert res["agent_utilization"]["claude-1"] == 1
    assert res["roi_hours_saved"] == 0.25


class DummyAnalyticsApp(App):
    def __init__(self, db_path: str):
        super().__init__()
        self.db_path = db_path

    def compose(self) -> ComposeResult:
        yield AnalyticsPanel(sqlite_path=self.db_path)


@pytest.mark.anyio
async def test_analytics_panel_mount(tmp_path):
    db_file = tmp_path / "audit.db"
    logger = LocalAuditLogger(sqlite_path=str(db_file), jsonl_path=str(tmp_path / "audit.jsonl"))
    logger.log(AuditEvent(
        session_id="s1",
        agent_id="claude-1",
        action="edit_file",
        outcome="success",
    ))

    app = DummyAnalyticsApp(str(db_file))
    async with app.run_test() as pilot:
        panel = app.query_one(AnalyticsPanel)
        assert panel is not None
        summary = app.query_one("#metrics-summary", Static)
        assert "Total Audit Events" in str(summary.render())
