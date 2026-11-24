from __future__ import annotations

from typing import Optional

from vad.memory.authority_guard import MemoryAuthorityGuard, MemoryAuthorityViolationError
from vad.memory.capabilities import MemoryCapabilities, ProviderHealth
from vad.memory.contracts import (
    AdvisoryContext,
    InferredLesson,
    LessonReviewStatus,
    MemoryEntry,
    MemoryQuery,
    MemoryScope,
    ObservedFact,
)
from vad.memory.provider import MemoryProviderProtocol
from vad.memory.providers.null import NullMemoryProvider
from vad.memory.redaction import Redactor
from vad.memory.stores.local import LocalMemoryStore


class MemoryGateway:
    """Gateway supporting both legacy LocalMemoryStore mode and v1 MemoryProviderProtocol mode."""

    def __init__(
        self,
        store: LocalMemoryStore | None = None,
        redactor: Redactor | None = None,
        provider: MemoryProviderProtocol | None = None,
        authority_guard: MemoryAuthorityGuard | None = None,
        fail_open: bool = True,
    ) -> None:
        self.store = store
        self.redactor = redactor or Redactor()
        self.provider: MemoryProviderProtocol = (
            provider if provider is not None else NullMemoryProvider()
        )
        self.authority_guard = authority_guard or MemoryAuthorityGuard()
        self.fail_open = fail_open

    # ------------------------------------------------------------------
    # Legacy LocalMemoryStore interface (backward-compatible)
    # ------------------------------------------------------------------
    def store_memory(self, entry: MemoryEntry) -> None:
        if self.store is None:
            raise RuntimeError("Legacy LocalMemoryStore is not configured on this gateway")
        entry.content = self.redactor.redact(entry.content)
        self.store.save(entry)

    def get_memory(self, entry_id: str, scope: MemoryScope) -> Optional[MemoryEntry]:
        if self.store is None:
            raise RuntimeError("Legacy LocalMemoryStore is not configured on this gateway")
        return self.store.retrieve(entry_id, scope)

    def get_by_scope(self, scope: MemoryScope) -> list[MemoryEntry]:
        if self.store is None:
            raise RuntimeError("Legacy LocalMemoryStore is not configured on this gateway")
        return self.store.get_by_scope(scope)

    # ------------------------------------------------------------------
    # v1 MemoryProviderProtocol interface
    # ------------------------------------------------------------------
    def capabilities(self) -> MemoryCapabilities:
        try:
            return self.provider.capabilities()
        except Exception:
            if not self.fail_open:
                raise
            return MemoryCapabilities(
                fact_storage=False,
                lesson_storage=False,
                scoped_query=False,
                health_check=False,
            )

    def health(self) -> ProviderHealth:
        try:
            return self.provider.health()
        except Exception as exc:
            if not self.fail_open:
                raise
            return ProviderHealth(
                healthy=False,
                provider_name=getattr(self.provider, "name", "unknown"),
                detail=str(exc),
            )

    def publish_fact(self, fact: ObservedFact) -> str | None:
        self.authority_guard.assert_read_only_for_authoritative_state("record_fact")
        redacted_fact = fact.model_copy(
            update={"summary": self.redactor.redact(fact.summary)}
        )
        validated_fact = self.authority_guard.validate_outbound_fact(redacted_fact)
        try:
            return self.provider.record_fact(validated_fact)
        except MemoryAuthorityViolationError:
            raise
        except Exception:
            if not self.fail_open:
                raise
            return None

    def publish_lesson(self, lesson: InferredLesson) -> str | None:
        self.authority_guard.assert_read_only_for_authoritative_state("record_lesson")
        redacted_lesson = lesson.model_copy(
            update={
                "title": self.redactor.redact(lesson.title),
                "recommendation": self.redactor.redact(lesson.recommendation),
            }
        )
        validated_lesson = self.authority_guard.validate_outbound_lesson(redacted_lesson)
        try:
            return self.provider.record_lesson(validated_lesson)
        except MemoryAuthorityViolationError:
            raise
        except Exception:
            if not self.fail_open:
                raise
            return None

    def promote_lesson(
        self,
        lesson_id: str,
        status: LessonReviewStatus,
        reviewed_by: str,
        promotion_record_id: str | None = None,
    ) -> InferredLesson:
        self.authority_guard.assert_read_only_for_authoritative_state("update_lesson_status")
        return self.provider.update_lesson_status(
            lesson_id=lesson_id,
            status=status,
            reviewed_by=reviewed_by,
            promotion_record_id=promotion_record_id,
        )

    def retrieve_advisory_context(self, query: MemoryQuery) -> AdvisoryContext:
        self.authority_guard.assert_read_only_for_authoritative_state("query_advisory_context")
        try:
            context = self.provider.query(query)
        except MemoryAuthorityViolationError:
            raise
        except Exception as exc:
            if not self.fail_open:
                raise
            return AdvisoryContext(
                provider_name=getattr(self.provider, "name", "unknown"),
                facts=[],
                approved_lessons=[],
                advisory_unapproved_lessons=[],
                degraded=True,
                degraded_reason=str(exc),
            )
        return self.authority_guard.validate_inbound_context(context)
