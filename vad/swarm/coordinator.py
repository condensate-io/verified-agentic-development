from pydantic import BaseModel, Field

from vad.swarm.agents import AgentCard, AgentRole
from vad.swarm.tasks import SwarmTaskGraph, SwarmTaskStatus
from vad.swarm.transport import LocalTransport, SwarmMessage


class SwarmCoordinatorResult(BaseModel):
    messages: list[SwarmMessage] = Field(default_factory=list)
    completed_task_ids: list[str] = Field(default_factory=list)


class LocalSwarmCoordinator:
    def __init__(self, agents: list[AgentCard], transport: LocalTransport | None = None):
        self.agents = agents
        self.transport = transport or LocalTransport()

    def run_ready_tasks(self, graph: SwarmTaskGraph) -> SwarmCoordinatorResult:
        messages = []
        completed = []
        for task in graph.ready_tasks():
            agent = self._agent_for_role(task.role)
            message = self.transport.publish(
                agent,
                _message_type_for_role(task.role),
                task.task_id,
                {"status": "completed", "description": task.description},
            )
            task.status = SwarmTaskStatus.COMPLETED
            messages.append(message)
            completed.append(task.task_id)
        return SwarmCoordinatorResult(messages=messages, completed_task_ids=completed)

    def _agent_for_role(self, role: AgentRole) -> AgentCard:
        for agent in self.agents:
            if agent.role == role:
                return agent
        raise ValueError(f"no agent available for role {role.value}")


def _message_type_for_role(role: AgentRole) -> str:
    return {
        AgentRole.PLANNER: "task_planned",
        AgentRole.BUILDER: "build_completed",
        AgentRole.VERIFIER: "verification_completed",
        AgentRole.AUDITOR: "audit_completed",
        AgentRole.RELEASE_GUARDIAN: "release_decision",
    }[role]
