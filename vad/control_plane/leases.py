from __future__ import annotations

from datetime import datetime, timedelta, timezone
from enum import Enum

from pydantic import BaseModel, Field, field_validator


class StaleFenceTokenError(ValueError):
    """Raised when a lease operation presents a stale or mismatched fence token."""


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
    fence_token: int = Field(default=1, ge=1)

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

    def assert_fence_valid(self, presented_token: int) -> None:
        """Verify that the presented fence token matches this lease's current token."""
        if presented_token < self.fence_token:
            raise StaleFenceTokenError(
                f"stale fence token {presented_token}: current lease fence_token is {self.fence_token}"
            )
        if presented_token > self.fence_token:
            raise StaleFenceTokenError(
                f"invalid fence token {presented_token}: exceeds current lease fence_token {self.fence_token}"
            )

    def renew(self, *, now: datetime, ttl_seconds: int, fence_token: int | None = None) -> "TaskLease":
        if fence_token is not None:
            self.assert_fence_valid(fence_token)
        if self.status != TaskLeaseStatus.ACTIVE:
            raise ValueError("only active leases can be renewed")
        self.expires_at = _normalize_time(now) + timedelta(seconds=ttl_seconds)
        return self

    def release(self, *, reason: str | None = None, fence_token: int | None = None) -> "TaskLease":
        if fence_token is not None:
            self.assert_fence_valid(fence_token)
        if self.status != TaskLeaseStatus.ACTIVE:
            raise ValueError("only active leases can be released")
        self.status = TaskLeaseStatus.RELEASED
        self.release_reason = reason
        return self

    def reissue_with_next_fence(
        self,
        *,
        client_id: str,
        actor: str,
        role: str | None = None,
        now: datetime | None = None,
        ttl_seconds: int = 300,
    ) -> "TaskLease":
        """Issue a new active lease for the same task and run with strictly incremented fence_token."""
        acquired_at = _normalize_time(now or datetime.now(timezone.utc))
        return TaskLease(
            task_id=self.task_id,
            run_id=self.run_id,
            client_id=client_id,
            role=role if role is not None else self.role,
            actor=actor,
            acquired_at=acquired_at,
            expires_at=acquired_at + timedelta(seconds=ttl_seconds),
            fence_token=self.fence_token + 1,
        )

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
    fence_token: int | None = Field(default=None, ge=1)

    @field_validator("task_id", "run_id", "client_id", "role", "actor")
    @classmethod
    def identifiers_must_be_safe(cls, value: str) -> str:
        return _safe_identifier(value)


class TaskLeaseRenewRequest(BaseModel):
    client_id: str = Field(min_length=1, max_length=160)
    ttl_seconds: int = Field(default=300, gt=0, le=86400)
    fence_token: int | None = Field(default=None, ge=1)

    @field_validator("client_id")
    @classmethod
    def client_id_must_be_safe(cls, value: str) -> str:
        return _safe_identifier(value)


class TaskLeaseReleaseRequest(BaseModel):
    client_id: str = Field(min_length=1, max_length=160)
    reason: str | None = Field(default=None, max_length=300)
    fence_token: int | None = Field(default=None, ge=1)

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


def new_task_lease(
    request: TaskLeaseAcquireRequest,
    *,
    now: datetime | None = None,
    previous_fence_token: int = 0,
) -> TaskLease:
    if request.fence_token is not None and request.fence_token <= previous_fence_token:
        raise StaleFenceTokenError(
            f"stale fence token {request.fence_token}: must exceed previous fence_token {previous_fence_token}"
        )
    fence_token = request.fence_token if request.fence_token is not None else max(1, previous_fence_token + 1)
    acquired_at = _normalize_time(now or datetime.now(timezone.utc))
    return TaskLease(
        task_id=request.task_id,
        run_id=request.run_id,
        client_id=request.client_id,
        role=request.role,
        actor=request.actor,
        acquired_at=acquired_at,
        expires_at=acquired_at + timedelta(seconds=request.ttl_seconds),
        fence_token=fence_token,
    )


def _normalize_time(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _safe_identifier(value: str) -> str:
    if any(separator in value for separator in ["/", "\\", "\n", "\r", "\t"]):
        raise ValueError("lease identifiers must not contain path or control separators")
    return value
