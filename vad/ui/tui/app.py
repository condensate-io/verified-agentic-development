"""Operator TUI: Interactive terminal user interface for governing autonomous agent execution."""

from typing import Optional
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Vertical
from textual.widgets import Footer, Header, Static, TabbedContent, TabPane

from vad.ui.tui.decisions_panel import DecisionsPanel
from vad.ui.tui.dep_graph_panel import DepGraphPanel
from vad.ui.tui.analytics_panel import AnalyticsCalculator, AnalyticsPanel
from vad.ui.tui.confirmation_panel import ConfirmationPanel


class OperatorTUIApp(App):
    """Main Textual Application for VAD Operators."""

    CSS = """
    Screen {
        layout: vertical;
    }
    #status-bar {
        dock: bottom;
        height: 1;
        background: $accent;
        color: $text;
        content-align: center middle;
    }
    TabbedContent {
        height: 1fr;
    }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit", show=True),
        Binding("d", "switch_tab('decisions')", "Decisions", show=True),
        Binding("g", "switch_tab('dep-graph')", "Dep-Graph", show=True),
        Binding("n", "switch_tab('analytics')", "Analytics", show=True),
        Binding("s", "switch_tab('staged')", "Staged Items", show=True),
        Binding("r", "refresh_all", "Refresh", show=True),
    ]

    def __init__(
        self,
        audit_db_path: str = "audit/audit.db",
        operator_id: str = "operator",
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.audit_db_path = audit_db_path
        self.operator_id = operator_id
        self.calculator = AnalyticsCalculator(sqlite_path=self.audit_db_path)

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with TabbedContent(initial="decisions"):
            with TabPane("Decisions (d)", id="decisions"):
                yield DecisionsPanel(id="panel-decisions")
            with TabPane("Dep-Graph (g)", id="dep-graph"):
                yield DepGraphPanel(id="panel-dep-graph")
            with TabPane("Analytics (n)", id="analytics"):
                yield AnalyticsPanel(sqlite_path=self.audit_db_path, id="panel-analytics")
            with TabPane("Staged Items (s)", id="staged"):
                yield ConfirmationPanel(operator_id=self.operator_id, id="panel-staged")
        yield Static(id="status-bar")
        yield Footer()

    def on_mount(self) -> None:
        self.title = "VAD Operator Console"
        self.update_status_bar()

    def update_status_bar(self) -> None:
        metrics = self.calculator.compute_metrics()
        status = self.query_one("#status-bar", Static)
        cost = metrics["estimated_cost_usd"]
        events = metrics["total_events"]
        status.update(
            f" [VAD Online] Events: {events} | Spend: ${cost:.4f} USD | Safety: Strict Human-Confirmed Push"
        )

    def action_switch_tab(self, tab_id: str) -> None:
        tabbed = self.query_one(TabbedContent)
        tabbed.active = tab_id

    def action_refresh_all(self) -> None:
        self.update_status_bar()
        try:
            self.query_one(AnalyticsPanel).refresh_analytics()
        except Exception:
            pass


def main() -> None:
    app = OperatorTUIApp()
    app.run()


if __name__ == "__main__":
    main()
