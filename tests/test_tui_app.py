import pytest
from textual.widgets import Header, Footer, TabbedContent
from vad.ui.tui.app import OperatorTUIApp
from vad.ui.tui.decisions_panel import DecisionsPanel
from vad.ui.tui.dep_graph_panel import DepGraphPanel
from vad.ui.tui.analytics_panel import AnalyticsPanel
from vad.ui.tui.confirmation_panel import ConfirmationPanel


@pytest.mark.anyio
async def test_operator_tui_app_mount(tmp_path):
    db_file = tmp_path / "test_audit.db"
    app = OperatorTUIApp(audit_db_path=str(db_file), operator_id="test-op")

    async with app.run_test() as pilot:
        # Check title and elements
        assert app.title == "VAD Operator Console"
        assert app.query_one(Header) is not None
        assert app.query_one(Footer) is not None
        assert app.query_one(TabbedContent) is not None

        # Verify all 4 tabs and panels exist
        assert app.query_one(DecisionsPanel) is not None
        assert app.query_one(DepGraphPanel) is not None
        assert app.query_one(AnalyticsPanel) is not None
        assert app.query_one(ConfirmationPanel) is not None

        # Test tab switching
        app.action_switch_tab("analytics")
        assert app.query_one(TabbedContent).active == "analytics"

        app.action_switch_tab("staged")
        assert app.query_one(TabbedContent).active == "staged"

        # Test status bar update
        app.action_refresh_all()
