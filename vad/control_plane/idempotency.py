from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _normalize_time(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _safe_identifier(value: str) -> str:
    if any(separator in value for separator in ["/", "\\", "\n", "\r", "\t"]):
        raise ValueError("idempotency identifiers must not contain path or control separators")
    return value


def compute_request_hash(payload: dict[str, Any]) -> str:
    """Compute a deterministic canonical JSON SHA-256 hex digest for a request payload."""
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class IdempotencyStatus(str, Enum):
    NEW = "new"
    REPLAY = "replay"
    CONFLICT = "conflict"


class IdempotencyRecord(BaseModel):
    idempotency_key: str = Field(min_length=1, max_length=160)
    route: str = Field(min_length=1, max_length=200)
    request_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    status_code: int = Field(ge=100, le=599)
    response_payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_utc_now)

    @field_validator("idempotency_key")
    @classmethod
    def idempotency_key_must_be_safe(cls, value: str) -> str:
        return _safe_identifier(value)

    @field_validator("route")
    @classmethod
    def route_must_not_contain_controls(cls, value: str) -> str:
        if any(separator in value for separator in ["\n", "\r", "\t"]):
            raise ValueError("idempotency route must not contain control separators")
        return value

    @field_validator("created_at", mode="after")
    @classmethod
    def created_at_must_be_utc(cls, value: datetime) -> datetime:
        return _normalize_time(value)


def evaluate_idempotency(
    existing: IdempotencyRecord | None,
    *,
    route: str,
    request_hash: str,
) -> IdempotencyStatus:
    """Evaluate an incoming request against an existing idempotency record."""
    if existing is None:
        return IdempotencyStatus.NEW
    if existing.route == route and hmac.compare_digest(existing.request_hash, request_hash):
        return IdempotencyStatus.REPLAY
    return IdempotencyStatus.CONFLICT
