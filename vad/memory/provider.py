from __future__ import annotations

from typing import Protocol, runtime_checkable

from vad.memory.capabilities import MemoryCapabilities, ProviderHealth
from vad.memory.contracts import (
    AdvisoryContext,
    InferredLesson,
    LessonReviewStatus,
    MemoryQuery,
    ObservedFact,
)


@runtime_checkable
class MemoryProviderProtocol(Protocol):
    name: str

    def capabilities(self) -> MemoryCapabilities: ...

    def health(self) -> ProviderHealth: ...

    def record_fact(self, fact: ObservedFact) -> str: ...

    def record_lesson(self, lesson: InferredLesson) -> str: ...

    def update_lesson_status(
        self,
        lesson_id: str,
        status: LessonReviewStatus,
        reviewed_by: str,
        promotion_record_id: str | None = None,
    ) -> InferredLesson: ...

    def query(self, query: MemoryQuery) -> AdvisoryContext: ...

    def list_facts(self, limit: int = 50) -> list[ObservedFact]: ...

    def list_lessons(
        self,
        status: LessonReviewStatus | None = None,
        limit: int = 50,
    ) -> list[InferredLesson]: ...
