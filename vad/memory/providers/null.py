from __future__ import annotations

from vad.memory.capabilities import MemoryCapabilities, ProviderHealth
from vad.memory.contracts import (
    AdvisoryContext,
    InferredLesson,
    LessonReviewStatus,
    MemoryQuery,
    ObservedFact,
)


class NullMemoryProvider:
    """No-op memory provider proving VAD operates cleanly without memory."""

    name: str = "null"

    def capabilities(self) -> MemoryCapabilities:
        return MemoryCapabilities(
            fact_storage=False,
            lesson_storage=False,
            scoped_query=False,
            health_check=True,
            supported_transports=["in_process"],
        )

    def health(self) -> ProviderHealth:
        return ProviderHealth(
            healthy=True,
            provider_name=self.name,
            detail="no-op null provider",
        )

    def record_fact(self, fact: ObservedFact) -> str:
        return fact.fact_id

    def record_lesson(self, lesson: InferredLesson) -> str:
        return lesson.lesson_id

    def update_lesson_status(
        self,
        lesson_id: str,
        status: LessonReviewStatus,
        reviewed_by: str,
        promotion_record_id: str | None = None,
    ) -> InferredLesson:
        raise KeyError(f"Lesson not found in NullMemoryProvider: {lesson_id}")

    def query(self, query: MemoryQuery) -> AdvisoryContext:
        return AdvisoryContext(
            provider_name=self.name,
            facts=[],
            approved_lessons=[],
            advisory_unapproved_lessons=[],
        )

    def list_facts(self, limit: int = 50) -> list[ObservedFact]:
        return []

    def list_lessons(
        self,
        status: LessonReviewStatus | None = None,
        limit: int = 50,
    ) -> list[InferredLesson]:
        return []
