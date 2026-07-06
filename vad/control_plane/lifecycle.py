from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator


class ControlPlaneState(str, Enum):
    STARTING = "starting"
    READY = "ready"
    DRAINING = "draining"
    STOPPED = "stopped"
    FAILED = "failed"


ALLOWED_TRANSITIONS: dict[ControlPlaneState, set[ControlPlaneState]] = {
    ControlPlaneState.STARTING: {ControlPlaneState.READY, ControlPlaneState.FAILED, ControlPlaneState.STOPPED},
    ControlPlaneState.READY: {ControlPlaneState.DRAINING, ControlPlaneState.FAILED},
    ControlPlaneState.DRAINING: {ControlPlaneState.STOPPED, ControlPlaneState.FAILED},
    ControlPlaneState.STOPPED: set(),
    ControlPlaneState.FAILED: set(),
}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ControlPlaneLifecycleEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: f"control-plane-event-{uuid4().hex}")
    from_state: ControlPlaneState | None = None
    to_state: ControlPlaneState
    reason: str
    actor: str = "control-plane"
    created_at: datetime = Field(default_factory=utc_now)

    @field_validator("reason", "actor")
    @classmethod
    def non_empty_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("value must not be empty")
        return value


class ControlPlaneLifecycle(BaseModel):
    state: ControlPlaneState = ControlPlaneState.STARTING
    events: list[ControlPlaneLifecycleEvent] = Field(default_factory=list)

    @classmethod
    def start(cls, reason: str = "control plane starting") -> "ControlPlaneLifecycle":
        lifecycle = cls()
        lifecycle.events.append(ControlPlaneLifecycleEvent(from_state=None, to_state=ControlPlaneState.STARTING, reason=reason))
        return lifecycle

    def transition(self, to_state: ControlPlaneState, reason: str, actor: str = "control-plane") -> ControlPlaneLifecycleEvent:
        allowed = ALLOWED_TRANSITIONS[self.state]
        if to_state not in allowed:
            raise ValueError(f"invalid control-plane transition: {self.state.value} -> {to_state.value}")
        event = ControlPlaneLifecycleEvent(from_state=self.state, to_state=to_state, reason=reason, actor=actor)
        self.state = to_state
        self.events.append(event)
        return event

    def mark_ready(self, reason: str = "control plane ready") -> ControlPlaneLifecycleEvent:
        return self.transition(ControlPlaneState.READY, reason)

    def request_shutdown(self, reason: str = "controlled shutdown requested", actor: str = "operator") -> ControlPlaneLifecycleEvent:
        return self.transition(ControlPlaneState.DRAINING, reason, actor=actor)

    def stop(self, reason: str = "control plane stopped") -> ControlPlaneLifecycleEvent:
        return self.transition(ControlPlaneState.STOPPED, reason)

    def fail(self, reason: str, actor: str = "control-plane") -> ControlPlaneLifecycleEvent:
        return self.transition(ControlPlaneState.FAILED, reason, actor=actor)


class ControlPlaneLock(BaseModel):
    instance_id: str = Field(default_factory=lambda: f"control-plane-{uuid4().hex}")
    process_id: int = Field(ge=0)
    state: ControlPlaneState
    started_at: datetime = Field(default_factory=utc_now)
    heartbeat_at: datetime = Field(default_factory=utc_now)


class LockRecoveryResult(BaseModel):
    recovered: bool
    reason: str
    event: ControlPlaneLifecycleEvent | None = None


def write_lock(path: str | Path, lock: ControlPlaneLock) -> None:
    lock_path = Path(path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text(lock.model_dump_json(indent=2), encoding="utf-8")


def read_lock(path: str | Path) -> ControlPlaneLock:
    return ControlPlaneLock.model_validate_json(Path(path).read_text(encoding="utf-8"))


def recover_stale_lock(path: str | Path, *, now: datetime | None = None, stale_after_seconds: int = 60) -> LockRecoveryResult:
    lock_path = Path(path)
    if not lock_path.exists():
        return LockRecoveryResult(recovered=False, reason="lock file does not exist")
    lock = read_lock(lock_path)
    current_time = now or utc_now()
    age = (current_time - lock.heartbeat_at).total_seconds()
    if age <= stale_after_seconds:
        return LockRecoveryResult(recovered=False, reason="lock heartbeat is still fresh")

    lock_path.unlink()
    event = ControlPlaneLifecycleEvent(
        from_state=lock.state,
        to_state=ControlPlaneState.STOPPED,
        reason=f"recovered stale control-plane lock after {int(age)} seconds",
        actor="recovery",
        created_at=current_time,
    )
    return LockRecoveryResult(recovered=True, reason="stale lock recovered", event=event)
