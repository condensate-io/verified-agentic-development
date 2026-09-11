"""Analytics Panel for Operator TUI: Computes and renders governance telemetry, cost, and utilization from SQLite audit logs."""

import json
from pathlib import Path
import sqlite3
from typing import Any, Dict, List, Optional
from textual.app import ComposeResult
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.widgets import DataTable, Label, Static


class AnalyticsCalculator:
    """Queries SQLite audit logs and calculates utilization, cost, and success metrics."""

    def __init__(self, sqlite_path: str = "audit/audit.db"):
        self.sqlite_path = sqlite_path

    def compute_metrics(self) -> Dict[str, Any]:
        p = Path(self.sqlite_path)
        if not p.exists():
            return {
                "total_events": 0,
                "agent_utilization": {},
                "success_rate": 0.0,
                "total_tokens": 0,
                "estimated_cost_usd": 0.0,
                "roi_hours_saved": 0.0,
            }

        with sqlite3.connect(self.sqlite_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM audit_events")
            total_events = cursor.fetchone()[0]

            cursor.execute("SELECT agent_id, COUNT(*) FROM audit_events GROUP BY agent_id")
            agent_counts = dict(cursor.fetchall())

            cursor.execute("SELECT outcome, COUNT(*) FROM audit_events GROUP BY outcome")
            outcome_counts = dict(cursor.fetchall())

            cursor.execute("SELECT metadata_json FROM audit_events")
            rows = cursor.fetchall()

        successes = outcome_counts.get("success", 0)
        failures = outcome_counts.get("failure", 0)
        total_outcomes = successes + failures
        success_rate = (successes / total_outcomes * 100.0) if total_outcomes > 0 else 100.0

        total_tokens = 0
        total_cost = 0.0
        for (m_json,) in rows:
            if m_json:
                try:
                    meta = json.loads(m_json)
                    total_tokens += meta.get("tokens", 0)
                    total_cost += meta.get("cost_usd", 0.0)
                except Exception:
                    pass

        # Fallback estimation if not explicitly logged
        if total_cost == 0.0 and total_events > 0:
            total_tokens = total_events * 450
            total_cost = (total_tokens / 1000.0) * 0.003

        # ROI heuristic: Each successful action saves ~15 minutes (0.25 hours) of manual human engineering
        roi_hours_saved = round(successes * 0.25, 2)

        return {
            "total_events": total_events,
            "agent_utilization": agent_counts,
            "success_rate": round(success_rate, 1),
            "total_tokens": total_tokens,
            "estimated_cost_usd": round(total_cost, 4),
            "roi_hours_saved": roi_hours_saved,
        }


class AnalyticsPanel(Container):
    """Textual widget displaying real-time analytics and cost accounting."""

    DEFAULT_CSS = """
    AnalyticsPanel {
        layout: vertical;
        height: 100%;
        padding: 1;
    }
    #metrics-summary {
        height: 40%;
        border: round cyan;
        padding: 1;
    }
    #agents-table {
        height: 60%;
    }
    """

    def __init__(self, sqlite_path: str = "audit/audit.db", **kwargs):
        super().__init__(**kwargs)
        self.calculator = AnalyticsCalculator(sqlite_path=sqlite_path)

    def compose(self) -> ComposeResult:
        yield Label("[bold yellow]VAD Governance & Telemetry Analytics[/bold yellow]")
        yield Static(id="metrics-summary")
        table = DataTable(id="agents-table")
        table.cursor_type = "row"
        yield table

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        table.add_columns("Agent ID", "Actions Executed", "Share (%)")
        self.refresh_analytics()

    def refresh_analytics(self) -> None:
        metrics = self.calculator.compute_metrics()
        summary_widget = self.query_one("#metrics-summary", Static)

        summary_text = (
            f"[bold]Total Audit Events:[/bold] {metrics['total_events']}\n"
            f"[bold]Success Rate:[/bold] {metrics['success_rate']}%\n"
            f"[bold]Estimated Spend:[/bold] ${metrics['estimated_cost_usd']:.4f} USD ({metrics['total_tokens']:,} tokens)\n"
            f"[bold]Engineering Hours Saved (ROI):[/bold] {metrics['roi_hours_saved']} hrs"
        )
        summary_widget.update(summary_text)

        table = self.query_one(DataTable)
        table.clear()
        total = metrics["total_events"] or 1
        for agent_id, count in metrics["agent_utilization"].items():
            share = round((count / total) * 100.0, 1)
            table.add_row(agent_id, str(count), f"{share}%")
