from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, Field, field_validator


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class OperatorIntentRecord(BaseModel):
    intent_ref: str = Field(min_length=1, max_length=200)
    actor: str = Field(min_length=1, max_length=160)
    role: str = Field(min_length=1, max_length=80)
    scope: str = Field(min_length=1, max_length=160)
    granted_tools: tuple[str, ...] = ()
    summary: str = Field(min_length=1, max_length=500)
    live_service_opt_in: bool = False
    high_risk: bool = False
    expires_at: datetime | None = None
    created_at: datetime = Field(default_factory=_utc_now)

    @field_validator("intent_ref", "actor", "role", "scope")
    @classmethod
    def identifiers_must_be_safe(cls, value: str) -> str:
        if any(separator in value for separator in ["/", "\\", "\n", "\r", "\t"]):
            raise ValueError("operator intent identifiers must not contain path or control separators")
        return value

    @field_validator("granted_tools")
    @classmethod
    def granted_tools_must_be_safe(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        for value in values:
            if any(separator in value for separator in ["/", "\\", "\n", "\r", "\t"]):
                raise ValueError("granted tool names must not contain path or control separators")
        return tuple(sorted(values))

    def is_active(self, *, now: datetime | None = None) -> bool:
        if self.expires_at is None:
            return True
        reference = now or _utc_now()
        expires = self.expires_at
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        return reference.astimezone(timezone.utc) <= expires.astimezone(timezone.utc)
