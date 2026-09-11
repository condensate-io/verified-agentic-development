"""Confirmation Panel for Operator TUI: Lists staged commits with diff preview and operator-confirmed push actions."""

from typing import Any, Callable, Dict, List, Optional
from textual.app import ComposeResult
from textual.containers import Container, Horizontal, Vertical
from textual.widgets import Button, DataTable, Label, Static

from vad.orchestrator.confirm_push import PushConfirmationController, PushConfirmationResult
from vad.orchestrator.dep_graph import DependencyGraph
from vad.orchestrator.staged_commit import StagedCommit, StagedCommitManager


class ConfirmationPanel(Container):
    """Enforces human-in-the-loop safety boundary by requiring explicit operator push authorization."""

    DEFAULT_CSS = """
    ConfirmationPanel {
        layout: vertical;
        height: 100%;
        padding: 1;
    }
    #staged-table {
        height: 50%;
    }
    #diff-preview {
        height: 35%;
        border: heavy magenta;
        padding: 1;
    }
    #confirmation-actions {
        height: 15%;
        align: center middle;
    }
    """

    def __init__(
        self,
        staged_manager: Optional[StagedCommitManager] = None,
        controller: Optional[PushConfirmationController] = None,
        operator_id: str = "operator",
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.graph = DependencyGraph()
        self.staged_manager = staged_manager or StagedCommitManager(graph=self.graph)
        self.controller = controller or PushConfirmationController(staged_manager=self.staged_manager)
        self.operator_id = operator_id

    def compose(self) -> ComposeResult:
        yield Label("[bold red]Staged Commits Awaiting Operator Push Authorization[/bold red]")
        table = DataTable(id="staged-table")
        table.cursor_type = "row"
        yield table
        yield Static(id="diff-preview")
        with Horizontal(id="confirmation-actions"):
            yield Button("Confirm & Push (p)", id="btn-confirm-push", variant="warning")
            yield Button("Refresh", id="btn-refresh-staged", variant="default")

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        table.add_columns("Staged ID", "Task ID", "Branch", "Commit SHA", "Summary", "Attested", "Status")
        self.refresh_staged_items()

    def refresh_staged_items(self) -> None:
        table = self.query_one(DataTable)
        table.clear()
        staged_items = self.staged_manager.get_staged_items(only_unconfirmed=False)

        for item in staged_items:
            has_attestation = "✓ DSSE" if item.dsse_envelope else "✗ Missing"
            status = "Pushed" if item.confirmed else "Staged"
            table.add_row(
                item.id,
                item.task_id,
                item.branch,
                item.commit_sha[:8],
                item.summary,
                has_attestation,
                status,
                key=item.id,
            )

        if staged_items:
            self.show_preview(staged_items[0])
        else:
            preview = self.query_one("#diff-preview", Static)
            preview.update("No commits currently staged for push.")

    def show_preview(self, item: StagedCommit) -> None:
        preview = self.query_one("#diff-preview", Static)
        text = (
            f"[bold cyan]Commit Preview:[/bold cyan] {item.commit_sha} on branch `{item.branch}`\n"
            f"[bold]Summary:[/bold] {item.summary}\n"
            f"[bold]Files Changed:[/bold] {', '.join(item.files_changed) or 'None'}\n"
            f"[bold]Diff Stat:[/bold] {item.diff_stat or '+0 -0'}"
        )
        preview.update(text)

    def confirm_selected_push(self) -> Optional[PushConfirmationResult]:
        table = self.query_one(DataTable)
        if table.row_count == 0:
            return None
        row_key, _ = table.coordinate_to_cell_key(table.cursor_coordinate)
        staged_id = str(row_key.value)

        result = self.controller.confirm_and_push(staged_id, operator_id=self.operator_id)
        self.refresh_staged_items()
        return result
