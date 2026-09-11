import pytest
from vad.orchestrator.dep_graph import DependencyGraph, TaskNode, TaskStatus
from vad.orchestrator.staged_commit import StagedCommitManager
from vad.review.dsse_attestation import DSSEAttestor


def test_staged_commit_halting_at_staged_state():
    graph = DependencyGraph()
    graph.add_task(TaskNode(id="TASK-DEPLOY", title="Deploy telephony config"))
    graph.lock("TASK-DEPLOY", "agent-builder")

    manager = StagedCommitManager(graph)
    attestor = DSSEAttestor()
    envelope = attestor.create_review_attestation(
        commit_sha="c0ffee1234567890c0ffee1234567890c0ffee12",
        review_decision="approved",
        reviewer_agent="verifier-1",
        proof_summary={"proofs": "all passed"},
    )

    staged = manager.stage_commit(
        task_id="TASK-DEPLOY",
        commit_sha="c0ffee1234567890c0ffee1234567890c0ffee12",
        branch="feature/telephony",
        summary="Add jitter buffer parameters",
        files_changed=["config/audio.yaml"],
        diff_stat="1 file changed, 10 insertions(+)",
        dsse_envelope=envelope,
    )

    # Invariant: DAG state must be STAGED (never DONE or auto-pushed)
    task = graph.get_task("TASK-DEPLOY")
    assert task.status == TaskStatus.STAGED

    unconfirmed = manager.get_staged_items(only_unconfirmed=True)
    assert len(unconfirmed) == 1
    assert unconfirmed[0].id == staged.id
    assert unconfirmed[0].confirmed is False
    assert unconfirmed[0].dsse_envelope is not None
