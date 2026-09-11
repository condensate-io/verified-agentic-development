import pytest
from vad.orchestrator.confirm_push import PushConfirmationController
from vad.orchestrator.dep_graph import DependencyGraph, TaskNode, TaskStatus
from vad.orchestrator.staged_commit import StagedCommitManager
from vad.review.dsse_attestation import DSSEAttestor


@pytest.fixture
def push_setup():
    graph = DependencyGraph()
    graph.add_task(TaskNode(id="T-PUSH", title="Task Ready for Push"))
    graph.lock("T-PUSH", "builder")

    manager = StagedCommitManager(graph)
    secret = b"test-push-signing-secret"
    attestor = DSSEAttestor()

    return graph, manager, attestor, secret


def test_confirm_push_requires_dsse_attestation(push_setup):
    graph, manager, attestor, secret = push_setup
    staged = manager.stage_commit(
        task_id="T-PUSH",
        commit_sha="abcd1234abcd1234abcd1234abcd1234abcd1234",
        branch="main",
        summary="Changes without attestation",
        files_changed=["file.py"],
        dsse_envelope=None,  # Missing!
    )

    controller = PushConfirmationController(manager, attestor=attestor, secret_key=secret)
    res = controller.confirm_and_push(staged.id, operator_id="operator-alice")
    assert res.pushed is False
    assert "missing mandatory DSSE review attestation" in res.reason


def test_confirm_push_succeeds_with_valid_attestation(push_setup):
    graph, manager, attestor, secret = push_setup
    commit_sha = "fade1234fade1234fade1234fade1234fade1234"

    envelope = attestor.create_review_attestation(
        commit_sha=commit_sha,
        review_decision="approved",
        reviewer_agent="adversarial-reviewer",
        proof_summary={"security": "passed"},
        secret_key=secret,
    )

    staged = manager.stage_commit(
        task_id="T-PUSH",
        commit_sha=commit_sha,
        branch="release/1.0",
        summary="Changes with attestation",
        files_changed=["file.py"],
        dsse_envelope=envelope,
    )

    pushed_args = []

    def mock_git_push(branch, sha):
        pushed_args.append((branch, sha))
        return True

    controller = PushConfirmationController(
        manager, attestor=attestor, git_push_fn=mock_git_push, secret_key=secret
    )
    res = controller.confirm_and_push(staged.id, operator_id="operator-bob")

    assert res.pushed is True
    assert res.confirmed_by == "operator-bob"
    assert ("release/1.0", commit_sha) in pushed_args

    # Check that DAG task was completed
    task = graph.get_task("T-PUSH")
    assert task.status == TaskStatus.DONE
