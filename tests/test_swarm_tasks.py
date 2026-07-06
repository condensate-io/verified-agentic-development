import pytest
from pydantic import ValidationError

from vad.swarm.agents import AgentRole
from vad.swarm.tasks import SwarmTask, SwarmTaskGraph, SwarmTaskStatus


def test_swarm_task_graph_dag_validates():
    graph = SwarmTaskGraph(tasks=[
        SwarmTask(task_id="plan", role=AgentRole.PLANNER, description="plan"),
        SwarmTask(task_id="build", role=AgentRole.BUILDER, description="build", depends_on=["plan"]),
    ])

    assert [task.task_id for task in graph.ready_tasks()] == ["plan"]


def test_swarm_task_graph_cycle_is_rejected():
    with pytest.raises(ValidationError):
        SwarmTaskGraph(tasks=[
            SwarmTask(task_id="a", role=AgentRole.PLANNER, description="a", depends_on=["b"]),
            SwarmTask(task_id="b", role=AgentRole.BUILDER, description="b", depends_on=["a"]),
        ])


def test_swarm_task_dependencies_are_enforced():
    graph = SwarmTaskGraph(tasks=[
        SwarmTask(task_id="plan", role=AgentRole.PLANNER, description="plan", status=SwarmTaskStatus.COMPLETED),
        SwarmTask(task_id="build", role=AgentRole.BUILDER, description="build", depends_on=["plan"]),
        SwarmTask(task_id="verify", role=AgentRole.VERIFIER, description="verify", depends_on=["build"]),
    ])

    assert [task.task_id for task in graph.ready_tasks()] == ["build"]


def test_swarm_task_unknown_dependency_fails_closed():
    with pytest.raises(ValidationError):
        SwarmTaskGraph(tasks=[
            SwarmTask(task_id="build", role=AgentRole.BUILDER, description="build", depends_on=["missing"]),
        ])
