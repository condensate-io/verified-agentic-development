from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field, field_validator, model_validator


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _normalize_time(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class WorkItemStatus(str, Enum):
    PLANNED = "planned"
    QUEUED = "queued"
    ASSIGNED = "assigned"
    RUNNING = "running"
    BLOCKED = "blocked"
    WAITING_FOR_HUMAN = "waiting_for_human"
    VERIFYING = "verifying"
    APPROVED = "approved"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    REQUEUED = "requeued"


class WorkItemGovernance(BaseModel):
    effort_type: str = Field(min_length=1, max_length=80)
    mees_estimate: int = Field(ge=0, le=100)
    token_budget: int = Field(ge=0)
    approval_required: bool = False
    live_service_opt_in: bool = False
    high_risk: bool = False
    operator_intent_ref: str | None = Field(default=None, max_length=200)
    approval_ref: str | None = Field(default=None, max_length=200)

    @field_validator("effort_type", "operator_intent_ref", "approval_ref")
    @classmethod
    def governance_text_must_be_safe(cls, value: str | None) -> str | None:
        if value is not None and any(separator in value for separator in ["/", "\\", "\n", "\r", "\t"]):
            raise ValueError("work item governance fields must not contain path or control separators")
        return value

    @model_validator(mode="after")
    def enforce_governance_controls(self) -> "WorkItemGovernance":
        if self.mees_estimate < 50 and not self.approval_required:
            raise ValueError("MEES under 50 requires work-item approval")
        if self.live_service_opt_in and not self.approval_required:
            raise ValueError("live-service opt-in requires work-item approval")
        if self.high_risk and not self.approval_required:
            raise ValueError("high-risk work requires work-item approval")
        if (self.live_service_opt_in or self.high_risk) and not self.operator_intent_ref:
            raise ValueError("live-service or high-risk work requires current operator intent reference")
        return self


class WorkItem(BaseModel):
    work_item_id: str = Field(min_length=1, max_length=160)
    run_id: str = Field(min_length=1, max_length=160)
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=2000)
    role: str = Field(min_length=1, max_length=80)
    requested_capability: str | None = Field(default=None, max_length=120)
    priority: int = Field(default=100, ge=0, le=1000)
    status: WorkItemStatus = WorkItemStatus.QUEUED
    created_at: datetime = Field(default_factory=_utc_now)
    updated_at: datetime = Field(default_factory=_utc_now)
    assigned_client_id: str | None = Field(default=None, max_length=160)
    lease_id: str | None = Field(default=None, max_length=160)
    evidence_digest: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    blocked_reason: str | None = Field(default=None, max_length=500)
    governance: WorkItemGovernance | None = None

    @field_validator(
        "work_item_id",
        "run_id",
        "role",
        "requested_capability",
        "assigned_client_id",
        "lease_id",
    )
    @classmethod
    def identifiers_must_be_safe(cls, value: str | None) -> str | None:
        if value is None:
            return value
        if any(separator in value for separator in ["/", "\\", "\n", "\r", "\t"]):
            raise ValueError("work item identifiers must not contain path or control separators")
        return value

    @field_validator("title", "description", "blocked_reason")
    @classmethod
    def text_must_not_contain_controls(cls, value: str | None) -> str | None:
        if value is not None and any(separator in value for separator in ["\n", "\r", "\t"]):
            raise ValueError("work item text fields must not contain control separators")
        return value

    @classmethod
    def json_schema(cls) -> dict:
        return cls.model_json_schema()


def update_work_item(
    item: WorkItem,
    *,
    status: WorkItemStatus | None = None,
    assigned_client_id: str | None = None,
    lease_id: str | None = None,
    evidence_digest: str | None = None,
    blocked_reason: str | None = None,
    now: datetime | None = None,
) -> WorkItem:
    data = item.model_dump()
    if status is not None:
        data["status"] = status
    if assigned_client_id is not None:
        data["assigned_client_id"] = assigned_client_id
    if lease_id is not None:
        data["lease_id"] = lease_id
    if evidence_digest is not None:
        data["evidence_digest"] = evidence_digest
    if blocked_reason is not None:
        data["blocked_reason"] = blocked_reason
    data["updated_at"] = _normalize_time(now or datetime.now(timezone.utc))
    return WorkItem(**data)
