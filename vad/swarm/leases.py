from datetime import datetime, timedelta

from pydantic import BaseModel, Field


class TaskLease(BaseModel):
    task_id: str
    holder_id: str
    expires_at: datetime
    retry_count: int = Field(default=0, ge=0)
    max_retries: int = Field(default=3, ge=0)
    blocked: bool = False
    blocker: str | None = None

    def heartbeat(self, now: datetime, ttl_seconds: int) -> "TaskLease":
        if self.blocked:
            return self
        self.expires_at = now + timedelta(seconds=ttl_seconds)
        return self

    def evaluate(self, now: datetime) -> "TaskLease":
        if self.blocked or now <= self.expires_at:
            return self
        self.retry_count += 1
        if self.retry_count > self.max_retries:
            self.blocked = True
            self.blocker = "retry limit exceeded"
        else:
            self.blocker = "missed heartbeat"
        return self

    def to_evidence(self) -> dict:
        return {
            "event": "task_lease",
            "task_id": self.task_id,
            "holder_id": self.holder_id,
            "retry_count": self.retry_count,
            "max_retries": self.max_retries,
            "blocked": self.blocked,
            "blocker": self.blocker,
        }
