from datetime import datetime
from uuid import uuid4

from pydantic import BaseModel, Field

from vad.evidence.bundle import EvidenceBundle
from vad.swarm.agents import AgentCard, AgentRole


ALLOWED_MESSAGE_TYPES = {
    AgentRole.PLANNER: {"task_planned"},
    AgentRole.BUILDER: {"build_completed"},
    AgentRole.VERIFIER: {"verification_completed"},
    AgentRole.AUDITOR: {"audit_completed"},
    AgentRole.RELEASE_GUARDIAN: {"release_decision"},
}


class SwarmMessage(BaseModel):
    message_id: str = Field(default_factory=lambda: str(uuid4()))
    sender_id: str
    sender_role: AgentRole
    message_type: str
    task_id: str
    payload: dict = Field(default_factory=dict)
    evidence_digest: str
    created_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())


class LocalTransport:
    def __init__(self):
        self.messages: list[SwarmMessage] = []

    def publish(self, sender: AgentCard, message_type: str, task_id: str, payload: dict) -> SwarmMessage:
        if message_type not in ALLOWED_MESSAGE_TYPES[sender.role]:
            raise PermissionError(f"{sender.role.value} cannot send {message_type}")
        message_payload = {
            "sender_id": sender.agent_id,
            "sender_role": sender.role.value,
            "message_type": message_type,
            "task_id": task_id,
            "payload": payload,
        }
        message = SwarmMessage(
            sender_id=sender.agent_id,
            sender_role=sender.role,
            message_type=message_type,
            task_id=task_id,
            payload=payload,
            evidence_digest=EvidenceBundle(message_payload).compute_hash(),
        )
        self.messages.append(message)
        return message

    def list_messages(self) -> list[SwarmMessage]:
        return list(self.messages)
