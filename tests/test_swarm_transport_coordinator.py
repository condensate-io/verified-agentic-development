import pytest

from vad.swarm.agents import AgentCard, AgentRole
from vad.swarm.coordinator import LocalSwarmCoordinator
from vad.swarm.tasks import SwarmTask, SwarmTaskGraph, SwarmTaskStatus
from vad.swarm.transport import LocalTransport


def agent(agent_id, role, capabilities):
    return AgentCard(agent_id=agent_id, role=role, capabilities=capabilities, model_tiers=["tier1"], autonomy_limit=1)


def test_planner_builder_verifier_auditor_exchange_messages():
    agents = [
        agent("planner", AgentRole.PLANNER, ["plan"]),
        agent("builder", AgentRole.BUILDER, ["modify_code"]),
        agent("verifier", AgentRole.VERIFIER, ["verify"]),
        agent("auditor", AgentRole.AUDITOR, ["audit_evidence"]),
    ]
    graph = SwarmTaskGraph(tasks=[
        SwarmTask(task_id="plan", role=AgentRole.PLANNER, description="plan"),
        SwarmTask(task_id="build", role=AgentRole.BUILDER, description="build", depends_on=["plan"]),
        SwarmTask(task_id="verify", role=AgentRole.VERIFIER, description="verify", depends_on=["build"]),
        SwarmTask(task_id="audit", role=AgentRole.AUDITOR, description="audit", depends_on=["verify"]),
    ])
    coordinator = LocalSwarmCoordinator(agents)

    completed = []
    for _ in range(4):
        result = coordinator.run_ready_tasks(graph)
        completed.extend(result.completed_task_ids)

    assert completed == ["plan", "build", "verify", "audit"]
    assert [message.message_type for message in coordinator.transport.list_messages()] == [
        "task_planned",
        "build_completed",
        "verification_completed",
        "audit_completed",
    ]


def test_unauthorized_message_is_denied():
    builder = agent("builder", AgentRole.BUILDER, ["modify_code"])
    transport = LocalTransport()

    with pytest.raises(PermissionError):
        transport.publish(builder, "release_decision", "release", {})


def test_all_swarm_messages_are_evidence_linked():
    planner = agent("planner", AgentRole.PLANNER, ["plan"])
    message = LocalTransport().publish(planner, "task_planned", "plan", {"status": "completed"})

    assert message.evidence_digest
    assert len(message.evidence_digest) == 64
