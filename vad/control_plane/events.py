from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator


class ControlPlaneEventKind(str, Enum):
    HEARTBEAT = "heartbeat"
    TOOL_CALL_STARTED = "tool_call_started"
    TOOL_CALL_FINISHED = "tool_call_finished"
    FILE_CHANGE_PROPOSED = "file_change_proposed"
    FILE_CHANGE_APPLIED = "file_change_applied"
    PROOF_STARTED = "proof_started"
    PROOF_FINISHED = "proof_finished"
    POLICY_DENIED = "policy_denied"
    APPROVAL_REQUESTED = "approval_requested"
    APPROVAL_RECORDED = "approval_recorded"
    SIGNER_EVENT = "signer_event"
    DEPLOYMENT_EVENT = "deployment_event"
    MESSAGE = "message"
    BLOCKER = "blocker"
    RECOVERY_ACTION = "recovery_action"
    SWARM = "swarm"
    PROVIDER = "provider"
    SIGNING = "signing"
    DEPLOYMENT = "deployment"
    WORK_ITEM = "work_item"
    TASK_LEASE = "task_lease"


class ControlPlaneEventStatus(str, Enum):
    ACTIVE = "active"
    PASSED = "passed"
    FAILED = "failed"
    BLOCKED = "blocked"
    NEEDS_HUMAN = "needs_human"
    STALE = "stale"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ControlPlaneEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: f"control-plane-event-{uuid4().hex}", max_length=120)
    sequence: int = Field(ge=0)
    created_at: datetime = Field(default_factory=utc_now)
    client_id: str = Field(min_length=1, max_length=160)
    client_label: str | None = Field(default=None, max_length=160)
    run_id: str | None = Field(default=None, max_length=160)
    task_id: str | None = Field(default=None, max_length=160)
    kind: ControlPlaneEventKind
    status: ControlPlaneEventStatus
    actor: str = Field(min_length=1, max_length=160)
    role: str = Field(min_length=1, max_length=80)
    evidence_digest: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    summary: str = Field(min_length=1, max_length=500)

    @field_validator("event_id", "client_id", "run_id", "task_id", "actor", "role")
    @classmethod
    def identifiers_must_be_safe(cls, value: str | None) -> str | None:
        if value is None:
            return value
        if any(separator in value for separator in ["/", "\\", "\n", "\r", "\t"]):
            raise ValueError("identifier must not contain path or control separators")
        return value

    @classmethod
    def json_schema(cls) -> dict:
        return cls.model_json_schema()
