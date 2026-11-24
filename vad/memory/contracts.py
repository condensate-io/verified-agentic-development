from __future__ import annotations

import re
from datetime import datetime, timezone
from enum import Enum
from typing import Any, ClassVar, Dict, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

MEMORY_INTERFACE_VERSION = "1.0.0"
_SHA256_HEX_RE = re.compile(r"^[0-9a-fA-F]{64}$")


class MemoryScope(BaseModel):
    owner: str
    purpose: str
    max_payload_size: int = Field(default=1024)

    RETROSPECTIVE: ClassVar["MemoryScope"]


class MemoryEntry(BaseModel):
    id: str
    scope: MemoryScope
    content: str
    metadata: Dict[str, Any] = Field(default_factory=dict)


MemoryScope.RETROSPECTIVE = MemoryScope(
    owner="system",
    purpose="retrospective",
    max_payload_size=8192,
)


class FactType(str, Enum):
    VERIFIED_DELIVERY = "verified_delivery"
    REJECTED_CHANGE = "rejected_change"
    VERIFICATION_FAILURE = "verification_failure"
    INCIDENT = "incident"
    POLICY_DENIAL = "policy_denial"


class ObservedFact(BaseModel):
    fact_id: str
    fact_type: FactType
    run_id: str
    eip_digest: str
    source_evidence_digest: str
    risk_tier: str
    domain_tags: list[str] = Field(default_factory=list)
    summary: str
    observed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, Any] = Field(default_factory=dict)
    authoritative_source: Literal["vad"] = "vad"

    @field_validator("source_evidence_digest")
    @classmethod
    def validate_source_evidence_digest(cls, value: str) -> str:
        if not _SHA256_HEX_RE.fullmatch(value):
            raise ValueError("source_evidence_digest must be a 64-character hex SHA-256 digest")
        return value.lower()


class LessonReviewStatus(str, Enum):
    PROPOSED = "proposed"
    UNDER_REVIEW = "under_review"
    APPROVED = "approved"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"


class ProposedRuleType(str, Enum):
    PROOF_OBLIGATION = "proof_obligation"
    INVARIANT = "invariant"
    RISK_CALIBRATION = "risk_calibration"
    TOOL_RESTRICTION = "tool_restriction"
    GENERAL_GUIDANCE = "general_guidance"


class InferredLesson(BaseModel):
    lesson_id: str
    title: str
    recommendation: str
    proposed_rule_type: ProposedRuleType = ProposedRuleType.GENERAL_GUIDANCE
    derived_from_fact_ids: list[str] = Field(default_factory=list)
    domain_tags: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    review_status: LessonReviewStatus = LessonReviewStatus.PROPOSED
    reviewed_by: str | None = None
    reviewed_at: datetime | None = None
    promotion_record_id: str | None = None

    @model_validator(mode="after")
    def validate_approval_requires_reviewer(self) -> "InferredLesson":
        if self.review_status == LessonReviewStatus.APPROVED:
            if not self.reviewed_by or not self.reviewed_by.strip():
                raise ValueError("approved lessons must specify a non-empty reviewed_by")
        return self

    @property
    def is_policy_eligible(self) -> bool:
        return (
            self.review_status == LessonReviewStatus.APPROVED
            and bool(self.reviewed_by and self.reviewed_by.strip())
        )


class MemoryQuery(BaseModel):
    query_text: str
    domain_tags: list[str] = Field(default_factory=list)
    fact_types: list[FactType] = Field(default_factory=list)
    include_unapproved_lessons: bool = False
    limit: int = Field(default=10, ge=1, le=100)


class AdvisoryContext(BaseModel):
    provider_name: str
    interface_version: str = MEMORY_INTERFACE_VERSION
    facts: list[ObservedFact] = Field(default_factory=list)
    approved_lessons: list[InferredLesson] = Field(default_factory=list)
    advisory_unapproved_lessons: list[InferredLesson] = Field(default_factory=list)
    degraded: bool = False
    degraded_reason: str | None = None
    advisory_only: Literal[True] = True
