from __future__ import annotations

import re
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator

_SHA256_HEX_RE = re.compile(r"^[a-f0-9]{64}$")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _normalize_time(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _safe_identifier(value: str) -> str:
    if any(separator in value for separator in ["/", "\\", "\n", "\r", "\t"]):
        raise ValueError("checkpoint identifiers must not contain path or control separators")
    return value


class CheckpointStage(str, Enum):
    EIP_CREATED = "eip_created"
    PROOF_PLANNED = "proof_planned"
    CONSTRUCTION_ASSIGNED = "construction_assigned"
    DIFF_PROPOSED = "diff_proposed"
    VERIFIED = "verified"
    POLICY_DECIDED = "policy_decided"
    RELEASED = "released"
    MEMORY_SYNCED = "memory_synced"


STAGE_ORDER: dict[CheckpointStage, int] = {
    CheckpointStage.EIP_CREATED: 1,
    CheckpointStage.PROOF_PLANNED: 2,
    CheckpointStage.CONSTRUCTION_ASSIGNED: 3,
    CheckpointStage.DIFF_PROPOSED: 4,
    CheckpointStage.VERIFIED: 5,
    CheckpointStage.POLICY_DECIDED: 6,
    CheckpointStage.RELEASED: 7,
    CheckpointStage.MEMORY_SYNCED: 8,
}

_ORDER_TO_STAGE: dict[int, CheckpointStage] = {order: stage for stage, order in STAGE_ORDER.items()}


class RunCheckpoint(BaseModel):
    checkpoint_id: str = Field(min_length=1, max_length=160)
    run_id: str = Field(min_length=1, max_length=160)
    work_item_id: str = Field(min_length=1, max_length=160)
    stage: CheckpointStage
    fence_token: int = Field(default=1, ge=1)
    actor: str = Field(min_length=1, max_length=160)
    role: str = Field(min_length=1, max_length=80)
    artifact_digests: dict[str, str] = Field(default_factory=dict)
    state_snapshot: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_utc_now)

    @field_validator("checkpoint_id", "run_id", "work_item_id", "actor", "role")
    @classmethod
    def identifiers_must_be_safe(cls, value: str) -> str:
        return _safe_identifier(value)

    @field_validator("artifact_digests")
    @classmethod
    def artifact_digests_must_be_sha256(cls, value: dict[str, str]) -> dict[str, str]:
        for name, digest in value.items():
            if not name or any(sep in name for sep in ["\n", "\r", "\t"]):
                raise ValueError("artifact digest key must be a non-empty string without control separators")
            if not _SHA256_HEX_RE.fullmatch(digest):
                raise ValueError(f"artifact digest for '{name}' must be a 64-character lowercase hex SHA-256")
        return value

    @field_validator("created_at", mode="after")
    @classmethod
    def created_at_must_be_utc(cls, value: datetime) -> datetime:
        return _normalize_time(value)


def next_resume_stage(latest: RunCheckpoint | None) -> CheckpointStage | None:
    """Return the next checkpoint stage to execute when resuming a run."""
    if latest is None:
        return CheckpointStage.EIP_CREATED
    current_order = STAGE_ORDER[latest.stage]
    return _ORDER_TO_STAGE.get(current_order + 1)
