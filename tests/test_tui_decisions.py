import pytest
from textual.app import App, ComposeResult
from vad.ui.tui.decisions_panel import DecisionsPanel


class DummyDecisionApp(App):
    def compose(self) -> ComposeResult:
        yield DecisionsPanel(id="decisions")


@pytest.mark.anyio
async def test_decisions_panel_mount_and_load():
    app = DummyDecisionApp()
    async with app.run_test() as pilot:
        panel = app.query_one(DecisionsPanel)
        assert panel is not None
        assert len(panel._pending_decisions) == 2


@pytest.mark.anyio
async def test_decisions_panel_resolve_action():
    resolved = {}

    def callback(dec_id, status):
        resolved[dec_id] = status

    class CallbackApp(App):
        def compose(self) -> ComposeResult:
            yield DecisionsPanel(id="decisions", on_decision_resolved=callback)

    app = CallbackApp()
    async with app.run_test() as pilot:
        panel = app.query_one(DecisionsPanel)
        dec_id = panel.resolve_selected(approved=True)
        assert dec_id == "DEC-101"
        assert resolved.get("DEC-101") == "approved"
