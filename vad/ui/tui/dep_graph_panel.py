"""Dependency Graph Panel for Operator TUI: Displays ASCII/Unicode DAG with color-coded status."""

from typing import Dict, List, Optional
from textual.app import ComposeResult
from textual.containers import Container, VerticalScroll
from textual.widgets import Label, Static

from vad.orchestrator.dep_graph import DependencyGraph, TaskNode, TaskStatus


STATUS_STYLES = {
    TaskStatus.READY: ("white", "[WHITE]"),
    TaskStatus.LOCKED: ("yellow", "[YELLOW]"),
    TaskStatus.STAGED: ("blue", "[BLUE]"),
    TaskStatus.DONE: ("green", "[GREEN]"),
    TaskStatus.FAILED: ("red", "[RED]"),
}


class DepGraphPanel(Container):
    """Visualizes DAG execution state with unicode hierarchy and status color tags."""

    DEFAULT_CSS = """
    DepGraphPanel {
        layout: vertical;
        height: 100%;
        padding: 1;
    }
    #dag-scroll {
        height: 100%;
        border: solid green;
    }
    """

    def __init__(self, graph: Optional[DependencyGraph] = None, **kwargs):
        super().__init__(**kwargs)
        self.graph = graph or DependencyGraph()

    def compose(self) -> ComposeResult:
        yield Label("[bold cyan]Task Dependency Graph (DAG)[/bold cyan]")
        with VerticalScroll(id="dag-scroll"):
            yield Static(id="dag-content")

    def on_mount(self) -> None:
        self.render_graph()

    def render_ascii_dag(self) -> str:
        """Generates an ASCII/Unicode representation of graph nodes and dependency links."""
        nodes = self.graph.all_tasks()
        if not nodes:
            return "No tasks registered in DAG."

        lines = ["┌── VAD Execution Pipeline"]
        # Find roots (no dependencies)
        roots = [n for n in nodes if not n.dependencies]
        visited = set()

        def render_node(node: TaskNode, prefix: str = "│  ", is_last: bool = False):
            visited.add(node.id)
            branch = "└── " if is_last else "├── "
            color, tag = STATUS_STYLES.get(node.status, ("white", "[WHITE]"))
            status_badge = f"[{color}][{node.status.value.upper()}][/{color}]"
            lines.append(f"{prefix}{branch}{status_badge} [bold]{node.id}[/bold] ({node.title or 'No desc'})")

            # Find children whose dependencies include this node
            children = [
                n for n in nodes
                if node.id in n.dependencies and n.id not in visited
            ]
            child_prefix = prefix + ("    " if is_last else "│   ")
            for idx, child in enumerate(children):
                render_node(child, child_prefix, idx == len(children) - 1)

        for idx, r in enumerate(roots):
            render_node(r, prefix="│  ", is_last=(idx == len(roots) - 1 and len(visited) >= len(nodes)))

        # Add remaining disconnected nodes if any
        remaining = [n for n in nodes if n.id not in visited]
        if remaining:
            lines.append("├── (Independent Tasks)")
            for idx, rem in enumerate(remaining):
                render_node(rem, prefix="│   ", is_last=(idx == len(remaining) - 1))

        lines.append("└── End of DAG")
        return "\n".join(lines)

    def render_graph(self) -> None:
        static_widget = self.query_one("#dag-content", Static)
        text = self.render_ascii_dag()
        static_widget.update(text)
