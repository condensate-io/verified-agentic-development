from __future__ import annotations

from datetime import datetime, timedelta, timezone
from enum import Enum

from pydantic import BaseModel, Field, field_validator


class TaskLeaseStatus(str, Enum):
    ACTIVE = "active"
    RELEASED = "released"
    EXPIRED = "expired"
    BLOCKED = "blocked"


class TaskLease(BaseModel):
    task_id: str = Field(min_length=1, max_length=160)
    run_id: str = Field(min_length=1, max_length=160)
    client_id: str = Field(min_length=1, max_length=160)
    role: str = Field(min_length=1, max_length=80)
    actor: str = Field(min_length=1, max_length=160)
    acquired_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: datetime
    status: TaskLeaseStatus = TaskLeaseStatus.ACTIVE
    release_reason: str | None = Field(default=None, max_length=300)

    @field_validator("task_id", "run_id", "client_id", "role", "actor")
    @classmethod
    def identifiers_must_be_safe(cls, value: str) -> str:
        if any(separator in value for separator in ["/", "\\", "\n", "\r", "\t"]):
            raise ValueError("lease identifiers must not contain path or control separators")
        return value

    @field_validator("release_reason")
    @classmethod
    def release_reason_must_not_contain_controls(cls, value: str | None) -> str | None:
        if value is not None and any(separator in value for separator in ["\n", "\r", "\t"]):
            raise ValueError("lease release reason must not contain control separators")
        return value

    def renew(self, *, now: datetime, ttl_seconds: int) -> "TaskLease":
        if self.status != TaskLeaseStatus.ACTIVE:
            raise ValueError("only active leases can be renewed")
        self.expires_at = _normalize_time(now) + timedelta(seconds=ttl_seconds)
        return self

    def release(self, *, reason: str | None = None) -> "TaskLease":
        if self.status != TaskLeaseStatus.ACTIVE:
            raise ValueError("only active leases can be released")
        self.status = TaskLeaseStatus.RELEASED
        self.release_reason = reason
        return self

    def expire(self, *, now: datetime) -> "TaskLease":
        if self.status != TaskLeaseStatus.ACTIVE:
            return self
        if _normalize_time(now) >= _normalize_time(self.expires_at):
            self.status = TaskLeaseStatus.EXPIRED
            self.release_reason = "lease expired"
        return self

    def assert_approval_transition_allowed(self, *, actor: str, role: str) -> None:
        if role != "release_guardian":
            raise ValueError("lease approval transition requires release_guardian role")
        if actor == self.actor or actor == self.client_id:
            raise ValueError("builder cannot approve own work through lease transition")


class TaskLeaseAcquireRequest(BaseModel):
    task_id: str = Field(min_length=1, max_length=160)
    run_id: str = Field(min_length=1, max_length=160)
    client_id: str = Field(min_length=1, max_length=160)
    role: str = Field(min_length=1, max_length=80)
    actor: str = Field(min_length=1, max_length=160)
    ttl_seconds: int = Field(default=300, gt=0, le=86400)

    @field_validator("task_id", "run_id", "client_id", "role", "actor")
    @classmethod
    def identifiers_must_be_safe(cls, value: str) -> str:
        return _safe_identifier(value)


class TaskLeaseRenewRequest(BaseModel):
    client_id: str = Field(min_length=1, max_length=160)
    ttl_seconds: int = Field(default=300, gt=0, le=86400)

    @field_validator("client_id")
    @classmethod
    def client_id_must_be_safe(cls, value: str) -> str:
        return _safe_identifier(value)


class TaskLeaseReleaseRequest(BaseModel):
    client_id: str = Field(min_length=1, max_length=160)
    reason: str | None = Field(default=None, max_length=300)

    @field_validator("client_id")
    @classmethod
    def client_id_must_be_safe(cls, value: str) -> str:
        return _safe_identifier(value)


class TaskLeaseApprovalCheck(BaseModel):
    actor: str = Field(min_length=1, max_length=160)
    role: str = Field(min_length=1, max_length=80)
    action: str = "approve_release"

    @field_validator("actor", "role")
    @classmethod
    def identifiers_must_be_safe(cls, value: str) -> str:
        return _safe_identifier(value)


def new_task_lease(request: TaskLeaseAcquireRequest, *, now: datetime | None = None) -> TaskLease:
    acquired_at = _normalize_time(now or datetime.now(timezone.utc))
    return TaskLease(
        task_id=request.task_id,
        run_id=request.run_id,
        client_id=request.client_id,
        role=request.role,
        actor=request.actor,
        acquired_at=acquired_at,
        expires_at=acquired_at + timedelta(seconds=request.ttl_seconds),
    )


def _normalize_time(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _safe_identifier(value: str) -> str:
    if any(separator in value for separator in ["/", "\\", "\n", "\r", "\t"]):
        raise ValueError("lease identifiers must not contain path or control separators")
    return value
