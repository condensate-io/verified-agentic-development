from __future__ import annotations

from enum import Enum
from typing import Any
from uuid import uuid4

from vad.memory.contracts import (
    FactType,
    InferredLesson,
    LessonReviewStatus,
    ObservedFact,
    ProposedRuleType,
)
from vad.memory.gateway import MemoryGateway
from vad.server.db.store import LessonPromotionRecord, ServerStore


class LearningLoopPublisher:
    """Publishes VAD run outcomes as ObservedFacts and candidate InferredLessons."""

    def __init__(
        self,
        memory_gateway: MemoryGateway,
        store: ServerStore | None = None,
    ) -> None:
        self.memory_gateway = memory_gateway
        self.store = store

    def _redact_text(self, text: str) -> str:
        redactor = getattr(self.memory_gateway, "redactor", None)
        if redactor is not None:
            return redactor.redact(text)
        return text

    def publish_run_outcome(
        self,
        *,
        run_id: str,
        eip_digest: str,
        evidence_digest: str,
        risk_tier: str,
        fact_type: FactType,
        summary: str,
        domain_tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ObservedFact:
        """Build a validated ObservedFact, publish via MemoryGateway, and return it."""
        risk_tier_value = risk_tier.value if isinstance(risk_tier, Enum) else str(risk_tier)
        fact = ObservedFact(
            fact_id=f"fact-{uuid4().hex[:12]}",
            fact_type=FactType(fact_type),
            run_id=run_id,
            eip_digest=eip_digest,
            source_evidence_digest=evidence_digest,
            risk_tier=risk_tier_value,
            domain_tags=list(domain_tags or []),
            summary=self._redact_text(summary),
            metadata=dict(metadata or {}),
            authoritative_source="vad",
        )
        self.memory_gateway.publish_fact(fact)
        return fact

    def publish_candidate_lesson(
        self,
        *,
        title: str,
        recommendation: str,
        proposed_rule_type: ProposedRuleType,
        derived_from_fact_ids: list[str],
        domain_tags: list[str] | None = None,
        confidence: float = 0.6,
    ) -> InferredLesson:
        """Publish an unapproved PROPOSED InferredLesson and record it in VAD's store if attached."""
        promotion_id = f"promo-{uuid4().hex[:12]}" if self.store is not None else None
        lesson = InferredLesson(
            lesson_id=f"lesson-{uuid4().hex[:12]}",
            title=self._redact_text(title),
            recommendation=self._redact_text(recommendation),
            proposed_rule_type=ProposedRuleType(proposed_rule_type),
            derived_from_fact_ids=list(derived_from_fact_ids),
            domain_tags=list(domain_tags or []),
            confidence=confidence,
            review_status=LessonReviewStatus.PROPOSED,
            reviewed_by=None,
            reviewed_at=None,
            promotion_record_id=promotion_id,
        )
        self.memory_gateway.publish_lesson(lesson)

        if self.store is not None:
            record = LessonPromotionRecord(
                promotion_id=promotion_id or f"promo-{uuid4().hex[:12]}",
                lesson_id=lesson.lesson_id,
                title=lesson.title,
                recommendation=lesson.recommendation,
                proposed_rule_type=lesson.proposed_rule_type.value,
                review_status=LessonReviewStatus.PROPOSED.value,
                reviewed_by=None,
                derived_from_fact_ids=list(lesson.derived_from_fact_ids),
                domain_tags=list(lesson.domain_tags),
                confidence=lesson.confidence,
                reviewed_at=None,
            )
            self.store.upsert_lesson_promotion(record)

        return lesson
