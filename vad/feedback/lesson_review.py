from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from vad.memory.authority_guard import MemoryAuthorityViolationError
from vad.memory.contracts import (
    InferredLesson,
    LessonReviewStatus,
    ProposedRuleType,
)
from vad.memory.gateway import MemoryGateway
from vad.server.db.store import LessonPromotionRecord, ServerStore

ALLOWED_REVIEWER_ROLES: frozenset[str] = frozenset({"release_guardian", "operator"})


class LessonReviewService:
    """Authoritative human review gate for promoting inferred lessons into active policy."""

    def __init__(
        self,
        store: ServerStore,
        memory_gateway: MemoryGateway | None = None,
    ) -> None:
        self.store = store
        self.memory_gateway = memory_gateway

    @staticmethod
    def _validate_reviewer(reviewer: str, reviewer_role: str) -> tuple[str, str]:
        normalized_reviewer = reviewer.strip() if isinstance(reviewer, str) else ""
        if not normalized_reviewer:
            raise ValueError("reviewer must be a non-empty string")
        normalized_role = reviewer_role.strip().lower() if isinstance(reviewer_role, str) else ""
        if normalized_role not in ALLOWED_REVIEWER_ROLES:
            raise PermissionError(
                f"Role {reviewer_role!r} is not authorized to review lessons; "
                f"allowed roles: {sorted(ALLOWED_REVIEWER_ROLES)}"
            )
        return normalized_reviewer, normalized_role

    def _sync_memory_status(
        self,
        *,
        lesson_id: str,
        status: LessonReviewStatus,
        reviewer: str,
        promotion_record_id: str,
    ) -> None:
        if self.memory_gateway is None:
            return
        try:
            self.memory_gateway.promote_lesson(
                lesson_id=lesson_id,
                status=status,
                reviewed_by=reviewer,
                promotion_record_id=promotion_record_id,
            )
        except MemoryAuthorityViolationError:
            raise
        except Exception:
            # VAD's SQLite store is authoritative; offline/unavailable memory providers
            # must not block human review decisions.
            return

    def _transition_status(
        self,
        lesson_id: str,
        *,
        status: LessonReviewStatus,
        reviewer: str,
        reviewer_role: str,
        metadata_updates: dict[str, str],
    ) -> LessonPromotionRecord:
        normalized_reviewer, normalized_role = self._validate_reviewer(reviewer, reviewer_role)
        existing = self.store.load_lesson_promotion(lesson_id)
        if existing is None:
            raise KeyError(f"Lesson promotion record not found for lesson_id={lesson_id!r}")

        now_iso = datetime.now(timezone.utc).isoformat()
        promotion_id = existing.promotion_id or f"promo-{uuid4().hex[:12]}"
        updated_metadata = dict(existing.metadata)
        updated_metadata["reviewer_role"] = normalized_role
        for key, value in metadata_updates.items():
            if value:
                updated_metadata[key] = value

        updated = existing.model_copy(
            update={
                "promotion_id": promotion_id,
                "review_status": status.value,
                "reviewed_by": normalized_reviewer,
                "reviewed_at": now_iso,
                "updated_at": now_iso,
                "metadata": updated_metadata,
            }
        )
        self.store.upsert_lesson_promotion(updated)
        self._sync_memory_status(
            lesson_id=lesson_id,
            status=status,
            reviewer=normalized_reviewer,
            promotion_record_id=updated.promotion_id,
        )
        return updated

    def submit_for_review(self, lesson: InferredLesson) -> LessonPromotionRecord:
        """Persist a candidate PROPOSED lesson in VAD's authoritative store for human review."""
        if (
            lesson.review_status == LessonReviewStatus.APPROVED
            or lesson.reviewed_by is not None
            or lesson.reviewed_at is not None
        ):
            raise MemoryAuthorityViolationError(
                f"Lesson {lesson.lesson_id!r} cannot be submitted pre-approved or pre-reviewed"
            )
        if lesson.review_status != LessonReviewStatus.PROPOSED:
            raise ValueError(
                f"Submitted lesson {lesson.lesson_id!r} must have review_status='proposed', "
                f"got {lesson.review_status.value!r}"
            )

        existing = self.store.load_lesson_promotion(lesson.lesson_id)
        if existing is not None and existing.review_status != LessonReviewStatus.PROPOSED.value:
            raise ValueError(
                f"Lesson {lesson.lesson_id!r} already has review_status={existing.review_status!r}"
            )

        promotion_id = (
            existing.promotion_id
            if existing is not None and existing.promotion_id
            else (lesson.promotion_record_id or f"promo-{uuid4().hex[:12]}")
        )
        record = LessonPromotionRecord(
            promotion_id=promotion_id,
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
        return self.store.upsert_lesson_promotion(record)

    def approve_lesson(
        self,
        lesson_id: str,
        *,
        reviewer: str,
        reviewer_role: str = "release_guardian",
        note: str = "",
    ) -> LessonPromotionRecord:
        """Approve a candidate lesson under human release_guardian/operator authority."""
        return self._transition_status(
            lesson_id,
            status=LessonReviewStatus.APPROVED,
            reviewer=reviewer,
            reviewer_role=reviewer_role,
            metadata_updates={"review_note": note},
        )

    def reject_lesson(
        self,
        lesson_id: str,
        *,
        reviewer: str,
        reviewer_role: str = "release_guardian",
        reason: str = "",
    ) -> LessonPromotionRecord:
        """Reject a candidate lesson so it cannot become active policy/proof input."""
        return self._transition_status(
            lesson_id,
            status=LessonReviewStatus.REJECTED,
            reviewer=reviewer,
            reviewer_role=reviewer_role,
            metadata_updates={"rejection_reason": reason},
        )

    def list_lessons(
        self,
        review_status: LessonReviewStatus | None = None,
    ) -> list[LessonPromotionRecord]:
        """List lesson promotion records from VAD's authoritative store."""
        status_filter = (
            review_status.value
            if isinstance(review_status, LessonReviewStatus)
            else review_status
        )
        return self.store.list_lesson_promotions(review_status=status_filter)

    def list_policy_eligible_lessons(self) -> list[InferredLesson]:
        """Return only lessons approved in VAD's authoritative store with a non-empty reviewer."""
        approved_records = self.store.list_lesson_promotions(
            review_status=LessonReviewStatus.APPROVED.value
        )
        eligible: list[InferredLesson] = []
        for record in approved_records:
            if record.review_status != LessonReviewStatus.APPROVED.value:
                continue
            if not record.reviewed_by or not record.reviewed_by.strip():
                continue
            reviewed_at = (
                datetime.fromisoformat(record.reviewed_at) if record.reviewed_at else None
            )
            lesson = InferredLesson(
                lesson_id=record.lesson_id,
                title=record.title,
                recommendation=record.recommendation,
                proposed_rule_type=ProposedRuleType(record.proposed_rule_type),
                derived_from_fact_ids=list(record.derived_from_fact_ids),
                domain_tags=list(record.domain_tags),
                confidence=record.confidence,
                review_status=LessonReviewStatus.APPROVED,
                reviewed_by=record.reviewed_by,
                reviewed_at=reviewed_at,
                promotion_record_id=record.promotion_id,
            )
            if lesson.is_policy_eligible:
                eligible.append(lesson)
        return eligible
