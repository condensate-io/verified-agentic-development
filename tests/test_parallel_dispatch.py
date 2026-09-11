import pytest
from vad.orchestrator.dep_graph import DependencyGraph, TaskStatus
from vad.orchestrator.parallel_dispatch import (
    ParallelDispatcher,
    SubtaskResult,
    SubtaskSpec,
)


def mock_runner(spec: SubtaskSpec) -> SubtaskResult:
    return SubtaskResult(
        subtask_id=spec.id,
        runner=spec.assigned_runner,
        domain=spec.domain,
        output=f"Resolved tasks for domain {spec.domain}.",
        exit_code=0,
        success=True,
    )


def test_parallel_dispatch_decomposition():
    dispatcher = ParallelDispatcher()
    prompt = "Configure OpenTelemetry metrics monitoring and update PostgreSQL database schema"
    subtasks = dispatcher.decompose(prompt)

    domains = [s.domain for s in subtasks]
    assert "telemetry" in domains
    assert "data" in domains
    assert len(subtasks) == 2


def test_parallel_dispatch_concurrent_execution():
    graph = DependencyGraph()
    dispatcher = ParallelDispatcher(graph=graph)

    prompt = "Audit JWT token security and analyze database query latency"
    subtasks = dispatcher.decompose(prompt)
    assert len(subtasks) >= 2

    dispatch_res = dispatcher.dispatch(
        dispatch_id="disp-101",
        prompt=prompt,
        subtasks=subtasks,
        runner_fn=mock_runner,
    )

    assert dispatch_res.all_succeeded is True
    assert len(dispatch_res.subtask_results) >= 2
    assert "Merged Dispatch Synthesis" in dispatch_res.merged_output

    # Check that DAG merge task was created and completed
    merge_task = graph.get_task("disp-101-merge")
    assert merge_task is not None
    assert merge_task.status == TaskStatus.DONE
