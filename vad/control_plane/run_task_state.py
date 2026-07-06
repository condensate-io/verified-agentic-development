from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, Field, field_validator

from vad.control_plane.work_items import WorkItem, WorkItemStatus


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class RunTaskState(BaseModel):
    run_id: str = Field(min_length=1, max_length=160)
    task_id: str = Field(min_length=1, max_length=160)
    work_item_id: str | None = Field(default=None, max_length=160)
    status: WorkItemStatus
    assigned_client_id: str | None = Field(default=None, max_length=160)
    lease_id: str | None = Field(default=None, max_length=160)
    blocked_reason: str | None = Field(default=None, max_length=500)
    evidence_digest: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    updated_at: datetime = Field(default_factory=_utc_now)

    @field_validator("run_id", "task_id", "work_item_id", "assigned_client_id", "lease_id")
    @classmethod
    def identifiers_must_be_safe(cls, value: str | None) -> str | None:
        if value is None:
            return value
        if any(separator in value for separator in ["/", "\\", "\n", "\r", "\t"]):
            raise ValueError("run task state identifiers must not contain path or control separators")
        return value

    @classmethod
    def from_work_item(cls, item: WorkItem) -> "RunTaskState":
        return cls(
            run_id=item.run_id,
            task_id=item.work_item_id,
            work_item_id=item.work_item_id,
            status=item.status,
            assigned_client_id=item.assigned_client_id,
            lease_id=item.lease_id,
            blocked_reason=item.blocked_reason,
            evidence_digest=item.evidence_digest,
            updated_at=item.updated_at,
        )
