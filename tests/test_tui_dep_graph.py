import pytest
from textual.app import App, ComposeResult
from textual.widgets import Static
from vad.orchestrator.dep_graph import DependencyGraph, TaskNode, TaskStatus
from vad.ui.tui.dep_graph_panel import DepGraphPanel


def test_dep_graph_ascii_rendering():
    graph = DependencyGraph()
    graph.add_task(TaskNode(id="A1", title="DoR Gate", status=TaskStatus.DONE))
    graph.add_task(TaskNode(id="B1", title="Dep Graph", status=TaskStatus.LOCKED, dependencies=["A1"]))
    graph.add_task(TaskNode(id="B5", title="Staged Commit", status=TaskStatus.STAGED, dependencies=["B1"]))
    graph.add_task(TaskNode(id="B6", title="Confirm Push", status=TaskStatus.READY, dependencies=["B5"]))

    panel = DepGraphPanel(graph=graph)
    rendered = panel.render_ascii_dag()

    assert "A1" in rendered
    assert "B1" in rendered
    assert "[DONE]" in rendered
    assert "[LOCKED]" in rendered
    assert "[STAGED]" in rendered
    assert "[READY]" in rendered


class DummyGraphApp(App):
    def __init__(self, graph: DependencyGraph):
        super().__init__()
        self.graph = graph

    def compose(self) -> ComposeResult:
        yield DepGraphPanel(graph=self.graph, id="dep-panel")


@pytest.mark.anyio
async def test_dep_graph_panel_mount():
    graph = DependencyGraph()
    graph.add_task(TaskNode(id="T1", title="Task 1", status=TaskStatus.READY))

    app = DummyGraphApp(graph)
    async with app.run_test() as pilot:
        panel = app.query_one(DepGraphPanel)
        assert panel is not None
        content = app.query_one("#dag-content", Static)
        assert "T1" in str(content.render())
