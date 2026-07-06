from datetime import datetime
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field

from vad.policy.engine import PolicyEngine


class A2AMessage(BaseModel):
    message_id: str = Field(default_factory=lambda: str(uuid4()))
    sender: str = Field(min_length=1)
    receiver: str = Field(min_length=1)
    action: str = Field(min_length=1)
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())


class A2AAdapter:
    def __init__(self, policy_path: str = "policies/vad.rego"):
        self.policy_path = policy_path
        self.policy = PolicyEngine()
        self.sent_messages: list[A2AMessage] = []
        self.evidence: list[dict[str, Any]] = []

    def send(self, message: A2AMessage) -> dict[str, Any]:
        capability = message.payload.get("capability", message.action)
        decision = self.policy.evaluate_decision(
            self.policy_path,
            {"action": "delegate", "capability": capability, "sender": message.sender, "receiver": message.receiver},
        )
        event = {
            "event": "a2a_send",
            "message": message.model_dump(mode="json"),
            "policy_decision": decision.model_dump(mode="json"),
        }
        self.evidence.append(event)
        if not decision.allow:
            return {"sent": False, "evidence": event}
        self.sent_messages.append(message)
        return {"sent": True, "message_id": message.message_id, "evidence": event}

    def send_message(self, agent_id: str, message: str) -> None:
        envelope = A2AMessage(sender="local", receiver=agent_id, action="message", payload={"message": message})
        self.send(envelope)
