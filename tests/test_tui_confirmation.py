import pytest
from textual.app import App, ComposeResult
from vad.orchestrator.dep_graph import DependencyGraph, TaskNode, TaskStatus
from vad.orchestrator.staged_commit import StagedCommitManager
from vad.orchestrator.confirm_push import PushConfirmationController
from vad.review.dsse_attestation import DSSEAttestor
from vad.ui.tui.confirmation_panel import ConfirmationPanel


class DummyConfirmApp(App):
    def __init__(self, manager: StagedCommitManager, controller: PushConfirmationController):
        super().__init__()
        self.manager = manager
        self.controller = controller

    def compose(self) -> ComposeResult:
        yield ConfirmationPanel(
            staged_manager=self.manager,
            controller=self.controller,
            operator_id="operator-alice",
            id="confirm-panel",
        )


@pytest.mark.anyio
async def test_confirmation_panel_mount_and_preview():
    graph = DependencyGraph()
    graph.add_task(TaskNode(id="T1", title="Task 1", status=TaskStatus.READY))
    manager = StagedCommitManager(graph=graph)

    attestor = DSSEAttestor()
    envelope = attestor.create_review_attestation(
        commit_sha="abcd1234ef567890",
        review_decision="approved",
        reviewer_agent="adversarial-verifier",
        proof_summary={"unit": "passed"},
        secret_key=b"vad-enterprise-signing-secret",
    )

    manager.stage_commit(
        task_id="T1",
        commit_sha="abcd1234ef567890",
        branch="feature/test",
        summary="Add feature test",
        files_changed=["vad/test.py"],
        diff_stat="+10 -2",
        dsse_envelope=envelope,
    )

    controller = PushConfirmationController(staged_manager=manager, git_push_fn=lambda b, s: True)
    app = DummyConfirmApp(manager, controller)

    async with app.run_test() as pilot:
        panel = app.query_one(ConfirmationPanel)
        assert panel is not None

        # Confirm push
        res = panel.confirm_selected_push()
        assert res is not None
        assert res.pushed is True
        assert res.confirmed_by == "operator-alice"
