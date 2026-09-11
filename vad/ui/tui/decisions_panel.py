"""Decisions Panel for Operator TUI: Displays pending and resolved governance decisions with approve/reject controls."""

from typing import Any, Callable, Dict, List, Optional
from textual.app import ComposeResult
from textual.containers import Container, Horizontal, Vertical
from textual.widgets import Button, DataTable, Label, Static

from vad.decision.catalog import DecisionCatalog, DecisionCategory, DecisionEntry


class DecisionsPanel(Container):
    """DataTable widget displaying decisions with approval actions."""

    DEFAULT_CSS = """
    DecisionsPanel {
        layout: vertical;
        height: 100%;
    }
    #decisions-table {
        height: 80%;
    }
    #actions-row {
        height: 20%;
        align: center middle;
    }
    """

    def __init__(
        self,
        catalog: Optional[DecisionCatalog] = None,
        on_decision_resolved: Optional[Callable[[str, str], None]] = None,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.catalog = catalog or DecisionCatalog()
        self.on_decision_resolved = on_decision_resolved
        self._pending_decisions: List[Dict[str, Any]] = []

    def compose(self) -> ComposeResult:
        yield Label("[bold]Governance Decision Queue[/bold]")
        table = DataTable(id="decisions-table")
        table.cursor_type = "row"
        yield table
        with Horizontal(id="actions-row"):
            yield Button("Approve (a)", id="btn-approve", variant="success")
            yield Button("Reject (r)", id="btn-reject", variant="error")
            yield Button("Refresh", id="btn-refresh", variant="primary")

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        table.add_columns("ID", "Type", "Requester", "Target", "Status")
        self.load_decisions()

    def load_decisions(self, decisions: Optional[List[Dict[str, Any]]] = None) -> None:
        table = self.query_one(DataTable)
        table.clear()
        if decisions is not None:
            self._pending_decisions = decisions
        else:
            # Default mock initial pending decisions
            self._pending_decisions = [
                {
                    "id": "DEC-101",
                    "type": "tool_elevation",
                    "requester": "claude-worker",
                    "target": "bash(git push)",
                    "status": "pending",
                },
                {
                    "id": "DEC-102",
                    "type": "scope_expansion",
                    "requester": "copilot-worker",
                    "target": "vad/policy/*",
                    "status": "pending",
                },
            ]

        for d in self._pending_decisions:
            table.add_row(
                d["id"],
                d["type"],
                d["requester"],
                d["target"],
                d["status"],
                key=d["id"],
            )

    def resolve_selected(self, approved: bool) -> Optional[str]:
        table = self.query_one(DataTable)
        if table.row_count == 0:
            return None
        row_key, _ = table.coordinate_to_cell_key(table.cursor_coordinate)
        decision_id = str(row_key.value)
        status = "approved" if approved else "rejected"

        for d in self._pending_decisions:
            if d["id"] == decision_id:
                d["status"] = status
                break

        if self.on_decision_resolved:
            self.on_decision_resolved(decision_id, status)

        self.load_decisions(self._pending_decisions)
        return decision_id
