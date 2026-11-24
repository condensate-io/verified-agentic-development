from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from vad.memory.capabilities import MemoryCapabilities, ProviderHealth
from vad.memory.contracts import (
    AdvisoryContext,
    InferredLesson,
    LessonReviewStatus,
    MemoryQuery,
    ObservedFact,
)


def _match_score(
    query_text: str,
    query_tags: set[str],
    searchable_texts: list[str],
    item_tags: list[str],
) -> int:
    """Return match score (>0 when matched, 0 when excluded)."""
    if not query_text and not query_tags:
        return 1

    item_tag_set = {tag.strip().lower() for tag in item_tags if tag.strip()}
    text_matched = False
    if query_text:
        text_matched = any(query_text in text.lower() for text in searchable_texts) or any(
            query_text in tag for tag in item_tag_set
        )

    tag_matched = bool(query_tags & item_tag_set) if query_tags else False

    if query_text and query_tags:
        if text_matched and tag_matched:
            return 3
        if text_matched or tag_matched:
            return 2
        return 0
    if query_text:
        return 1 if text_matched else 0
    return 1 if tag_matched else 0


class LocalMemoryProvider:
    """In-memory / optional local JSON provider with strict fact-vs-lesson separation."""

    name: str = "local"

    def __init__(self, local_path: str | Path | None = None) -> None:
        self.local_path: Path | None = Path(local_path) if local_path else None
        self._facts: dict[str, ObservedFact] = {}
        self._lessons: dict[str, InferredLesson] = {}
        if self.local_path and self.local_path.exists():
            self._load()

    def capabilities(self) -> MemoryCapabilities:
        return MemoryCapabilities(
            fact_storage=True,
            lesson_storage=True,
            scoped_query=True,
            health_check=True,
            semantic_search=False,
            causal_graph=False,
            contradiction_resolution=False,
            context_consolidation=False,
            provenance_traversal=False,
            supported_transports=["in_process"],
        )

    def health(self) -> ProviderHealth:
        detail = (
            f"local json provider ({self.local_path})"
            if self.local_path
            else "in-memory local provider"
        )
        return ProviderHealth(
            healthy=True,
            provider_name=self.name,
            detail=detail,
        )

    def record_fact(self, fact: ObservedFact) -> str:
        self._facts[fact.fact_id] = fact.model_copy(deep=True)
        self._persist()
        return fact.fact_id

    def record_lesson(self, lesson: InferredLesson) -> str:
        self._lessons[lesson.lesson_id] = lesson.model_copy(deep=True)
        self._persist()
        return lesson.lesson_id

    def update_lesson_status(
        self,
        lesson_id: str,
        status: LessonReviewStatus,
        reviewed_by: str,
        promotion_record_id: str | None = None,
    ) -> InferredLesson:
        existing = self._lessons.get(lesson_id)
        if existing is None:
            raise KeyError(f"Lesson not found: {lesson_id}")

        payload = existing.model_dump()
        payload["review_status"] = status
        payload["reviewed_by"] = reviewed_by
        payload["reviewed_at"] = datetime.now(timezone.utc)
        if promotion_record_id is not None:
            payload["promotion_record_id"] = promotion_record_id

        updated = InferredLesson.model_validate(payload)
        self._lessons[lesson_id] = updated
        self._persist()
        return updated.model_copy(deep=True)

    def query(self, query: MemoryQuery) -> AdvisoryContext:
        query_text = query.query_text.strip().lower()
        query_tags = {tag.strip().lower() for tag in query.domain_tags if tag.strip()}

        scored_facts: list[tuple[int, ObservedFact]] = []
        for fact in self._facts.values():
            if query.fact_types and fact.fact_type not in query.fact_types:
                continue
            score = _match_score(
                query_text=query_text,
                query_tags=query_tags,
                searchable_texts=[fact.summary, fact.fact_type.value],
                item_tags=fact.domain_tags,
            )
            if score > 0:
                scored_facts.append((score, fact.model_copy(deep=True)))

        scored_facts.sort(key=lambda item: item[0], reverse=True)
        matched_facts = [fact for _, fact in scored_facts[: query.limit]]

        scored_approved: list[tuple[int, InferredLesson]] = []
        scored_unapproved: list[tuple[int, InferredLesson]] = []
        for lesson in self._lessons.values():
            score = _match_score(
                query_text=query_text,
                query_tags=query_tags,
                searchable_texts=[lesson.title, lesson.recommendation],
                item_tags=lesson.domain_tags,
            )
            if score <= 0:
                continue
            lesson_copy = lesson.model_copy(deep=True)
            if lesson_copy.is_policy_eligible:
                scored_approved.append((score, lesson_copy))
            elif query.include_unapproved_lessons:
                scored_unapproved.append((score, lesson_copy))

        scored_approved.sort(key=lambda item: item[0], reverse=True)
        scored_unapproved.sort(key=lambda item: item[0], reverse=True)

        return AdvisoryContext(
            provider_name=self.name,
            facts=matched_facts,
            approved_lessons=[lesson for _, lesson in scored_approved[: query.limit]],
            advisory_unapproved_lessons=[
                lesson for _, lesson in scored_unapproved[: query.limit]
            ],
        )

    def list_facts(self, limit: int = 50) -> list[ObservedFact]:
        if limit <= 0:
            return []
        return [fact.model_copy(deep=True) for fact in list(self._facts.values())[:limit]]

    def list_lessons(
        self,
        status: LessonReviewStatus | None = None,
        limit: int = 50,
    ) -> list[InferredLesson]:
        if limit <= 0:
            return []
        lessons = [
            lesson.model_copy(deep=True)
            for lesson in self._lessons.values()
            if status is None or lesson.review_status == status
        ]
        return lessons[:limit]

    def _load(self) -> None:
        if not self.local_path:
            return
        raw = json.loads(self.local_path.read_text(encoding="utf-8"))
        self._facts = {
            fact_id: ObservedFact.model_validate(fact_data)
            for fact_id, fact_data in raw.get("facts", {}).items()
        }
        self._lessons = {
            lesson_id: InferredLesson.model_validate(lesson_data)
            for lesson_id, lesson_data in raw.get("lessons", {}).items()
        }

    def _persist(self) -> None:
        if not self.local_path:
            return
        self.local_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "facts": {
                fact_id: fact.model_dump(mode="json")
                for fact_id, fact in self._facts.items()
            },
            "lessons": {
                lesson_id: lesson.model_dump(mode="json")
                for lesson_id, lesson in self._lessons.items()
            },
        }
        self.local_path.write_text(
            json.dumps(payload, indent=2, sort_keys=True),
            encoding="utf-8",
        )
