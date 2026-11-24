from __future__ import annotations

import json

import pytest

from vad.memory.authority_guard import (
    FORBIDDEN_AUTHORITY_FIELDS,
    MemoryAuthorityGuard,
    MemoryAuthorityViolationError,
)
from vad.memory.contracts import (
    AdvisoryContext,
    FactType,
    InferredLesson,
    LessonReviewStatus,
    MemoryEntry,
    MemoryQuery,
    MemoryScope,
    ObservedFact,
    ProposedRuleType,
)
from vad.memory.gateway import MemoryGateway
from vad.memory.provider import MemoryProviderProtocol
from vad.memory.providers import LocalMemoryProvider, NullMemoryProvider
from vad.memory.redaction import Redactor
from vad.memory.stores.local import LocalMemoryStore

VALID_DIGEST = "a" * 64


def _make_fact(
    fact_id: str = "fact-1",
    summary: str = "Verification failed due to missing proof",
    fact_type: FactType = FactType.VERIFICATION_FAILURE,
    domain_tags: list[str] | None = None,
    metadata: dict | None = None,
) -> ObservedFact:
    return ObservedFact(
        fact_id=fact_id,
        fact_type=fact_type,
        run_id="run-1",
        eip_digest="b" * 64,
        source_evidence_digest=VALID_DIGEST,
        risk_tier="high",
        domain_tags=domain_tags or ["auth", "proof"],
        summary=summary,
        metadata=metadata or {},
    )


def _make_lesson(
    lesson_id: str = "lesson-1",
    title: str = "Require auth proof obligation",
    recommendation: str = "Add unit proof for token expiry checks",
    domain_tags: list[str] | None = None,
    review_status: LessonReviewStatus = LessonReviewStatus.PROPOSED,
    reviewed_by: str | None = None,
) -> InferredLesson:
    return InferredLesson(
        lesson_id=lesson_id,
        title=title,
        recommendation=recommendation,
        proposed_rule_type=ProposedRuleType.PROOF_OBLIGATION,
        derived_from_fact_ids=["fact-1"],
        domain_tags=domain_tags or ["auth"],
        review_status=review_status,
        reviewed_by=reviewed_by,
    )


class _OfflineProvider:
    name = "offline"

    def capabilities(self):
        raise ConnectionError("memory backend offline")

    def health(self):
        raise ConnectionError("memory backend offline")

    def record_fact(self, fact: ObservedFact) -> str:
        raise ConnectionError("memory backend offline")

    def record_lesson(self, lesson: InferredLesson) -> str:
        raise ConnectionError("memory backend offline")

    def update_lesson_status(
        self,
        lesson_id: str,
        status: LessonReviewStatus,
        reviewed_by: str,
        promotion_record_id: str | None = None,
    ) -> InferredLesson:
        raise ConnectionError("memory backend offline")

    def query(self, query: MemoryQuery) -> AdvisoryContext:
        raise ConnectionError("memory backend offline")

    def list_facts(self, limit: int = 50) -> list[ObservedFact]:
        return []

    def list_lessons(
        self, status: LessonReviewStatus | None = None, limit: int = 50
    ) -> list[InferredLesson]:
        return []


class _SmugglingProvider(NullMemoryProvider):
    name = "smuggler"

    def __init__(self, context: AdvisoryContext) -> None:
        self._context = context

    def query(self, query: MemoryQuery) -> AdvisoryContext:
        return self._context


def test_null_memory_provider_protocol_and_behavior() -> None:
    provider = NullMemoryProvider()
    assert isinstance(provider, MemoryProviderProtocol)

    caps = provider.capabilities()
    assert caps.fact_storage is False
    assert caps.lesson_storage is False
    assert caps.scoped_query is False
    assert caps.health_check is True
    assert caps.supported_transports == ["in_process"]

    health = provider.health()
    assert health.healthy is True
    assert health.provider_name == "null"

    assert provider.record_fact(_make_fact()) == "fact-1"
    assert provider.record_lesson(_make_lesson()) == "lesson-1"
    assert provider.list_facts() == []
    assert provider.list_lessons() == []

    ctx = provider.query(MemoryQuery(query_text="auth", include_unapproved_lessons=True))
    assert ctx.provider_name == "null"
    assert ctx.facts == []
    assert ctx.approved_lessons == []
    assert ctx.advisory_unapproved_lessons == []

    with pytest.raises(KeyError):
        provider.update_lesson_status(
            "lesson-1", LessonReviewStatus.APPROVED, reviewed_by="reviewer-1"
        )


def test_local_memory_provider_persistence_separation_and_promotion(tmp_path) -> None:
    store_file = tmp_path / "local_memory.json"
    provider = LocalMemoryProvider(local_path=store_file)
    assert isinstance(provider, MemoryProviderProtocol)

    caps = provider.capabilities()
    assert caps.fact_storage is True
    assert caps.lesson_storage is True
    assert caps.scoped_query is True
    assert caps.health_check is True
    assert caps.semantic_search is False
    assert caps.causal_graph is False
    assert caps.supported_transports == ["in_process"]

    fact_auth = _make_fact(
        fact_id="f-auth",
        summary="Token validation failed",
        fact_type=FactType.VERIFICATION_FAILURE,
        domain_tags=["auth"],
    )
    fact_db = _make_fact(
        fact_id="f-db",
        summary="Migration rollback verified",
        fact_type=FactType.VERIFIED_DELIVERY,
        domain_tags=["database"],
    )
    lesson_unapproved = _make_lesson(
        lesson_id="l-1",
        title="Enforce token TTL invariant",
        recommendation="Validate token expiry in auth middleware",
        domain_tags=["auth"],
    )
    lesson_approved = _make_lesson(
        lesson_id="l-2",
        title="Verify auth claims",
        recommendation="Always check audience claim",
        domain_tags=["auth"],
    )

    provider.record_fact(fact_auth)
    provider.record_fact(fact_db)
    provider.record_lesson(lesson_unapproved)
    provider.record_lesson(lesson_approved)

    promoted = provider.update_lesson_status(
        "l-2",
        LessonReviewStatus.APPROVED,
        reviewed_by="alice",
        promotion_record_id="promo-42",
    )
    assert promoted.is_policy_eligible is True
    assert promoted.reviewed_by == "alice"
    assert promoted.promotion_record_id == "promo-42"
    assert promoted.reviewed_at is not None

    # Verify JSON file keeps facts and lessons strictly separated
    raw = json.loads(store_file.read_text(encoding="utf-8"))
    assert set(raw.keys()) == {"facts", "lessons"}
    assert set(raw["facts"].keys()) == {"f-auth", "f-db"}
    assert set(raw["lessons"].keys()) == {"l-1", "l-2"}

    # Reload from disk
    reloaded = LocalMemoryProvider(local_path=store_file)
    assert len(reloaded.list_facts()) == 2
    assert len(reloaded.list_lessons()) == 2
    assert [l.lesson_id for l in reloaded.list_lessons(status=LessonReviewStatus.APPROVED)] == ["l-2"]

    # Query without unapproved lessons
    ctx_approved_only = reloaded.query(
        MemoryQuery(
            query_text="token",
            domain_tags=["auth"],
            fact_types=[FactType.VERIFICATION_FAILURE],
            include_unapproved_lessons=False,
        )
    )
    assert [f.fact_id for f in ctx_approved_only.facts] == ["f-auth"]
    assert [l.lesson_id for l in ctx_approved_only.approved_lessons] == ["l-2"]
    assert ctx_approved_only.advisory_unapproved_lessons == []

    # Query with unapproved lessons included
    ctx_with_unapproved = reloaded.query(
        MemoryQuery(
            query_text="token",
            domain_tags=["auth"],
            include_unapproved_lessons=True,
        )
    )
    assert [l.lesson_id for l in ctx_with_unapproved.approved_lessons] == ["l-2"]
    assert [l.lesson_id for l in ctx_with_unapproved.advisory_unapproved_lessons] == ["l-1"]

    with pytest.raises(KeyError):
        reloaded.update_lesson_status("missing", LessonReviewStatus.REJECTED, "alice")


def test_authority_guard_blocks_smuggled_lessons_and_forbidden_metadata() -> None:
    guard = MemoryAuthorityGuard()

    # Smuggled unapproved lesson inside approved_lessons
    unapproved = _make_lesson(
        lesson_id="smuggled-1",
        review_status=LessonReviewStatus.PROPOSED,
    )
    smuggled_ctx = AdvisoryContext(
        provider_name="evil",
        approved_lessons=[unapproved],
    )
    with pytest.raises(MemoryAuthorityViolationError, match="approved_lessons"):
        guard.validate_inbound_context(smuggled_ctx)

    # Approved lesson constructed without reviewer via model_construct
    reviewerless_approved = InferredLesson.model_construct(
        lesson_id="smuggled-2",
        title="Bypass",
        recommendation="No reviewer",
        proposed_rule_type=ProposedRuleType.GENERAL_GUIDANCE,
        derived_from_fact_ids=[],
        domain_tags=[],
        confidence=0.5,
        review_status=LessonReviewStatus.APPROVED,
        reviewed_by="   ",
    )
    with pytest.raises(MemoryAuthorityViolationError, match="approved_lessons"):
        guard.validate_inbound_context(
            AdvisoryContext.model_construct(
                provider_name="evil",
                approved_lessons=[reviewerless_approved],
                advisory_only=True,
            )
        )

    # Context with advisory_only=False rejected
    with pytest.raises(MemoryAuthorityViolationError, match="advisory_only"):
        guard.validate_inbound_context(
            AdvisoryContext.model_construct(
                provider_name="evil",
                advisory_only=False,
            )
        )

    # Forbidden authority-override metadata fields on facts
    for field in FORBIDDEN_AUTHORITY_FIELDS:
        bad_fact = _make_fact(metadata={field: True})
        with pytest.raises(MemoryAuthorityViolationError, match=field):
            guard.validate_inbound_context(
                AdvisoryContext(provider_name="evil", facts=[bad_fact])
            )
        with pytest.raises(MemoryAuthorityViolationError, match=field):
            guard.validate_outbound_fact(bad_fact)

    # Nested forbidden metadata
    nested_bad_fact = _make_fact(metadata={"nested": {"bypass_gate": "yes"}})
    with pytest.raises(MemoryAuthorityViolationError, match="bypass_gate"):
        guard.validate_inbound_context(
            AdvisoryContext(provider_name="evil", facts=[nested_bad_fact])
        )

    # Outbound fact validation against forged authoritative_source or invalid digest
    forged_fact = ObservedFact.model_construct(
        fact_id="forged",
        fact_type=FactType.INCIDENT,
        run_id="r1",
        eip_digest="b" * 64,
        source_evidence_digest="not-a-digest",
        risk_tier="high",
        domain_tags=[],
        summary="forged",
        metadata={},
        authoritative_source="external_memory",
    )
    with pytest.raises(MemoryAuthorityViolationError):
        guard.validate_outbound_fact(forged_fact)

    # Authoritative state mutation guard
    for op in [
        "mutate_evidence_ledger",
        "update_verification_result",
        "grant_approval_record",
        "override_policy_decision",
    ]:
        with pytest.raises(MemoryAuthorityViolationError):
            guard.assert_read_only_for_authoritative_state(op)

    guard.assert_read_only_for_authoritative_state("record_fact")
    guard.assert_read_only_for_authoritative_state("query_advisory_context")


def test_memory_gateway_redaction_degradation_and_legacy_compatibility() -> None:
    # Default gateway uses NullMemoryProvider
    default_gw = MemoryGateway()
    assert default_gw.provider.name == "null"
    assert default_gw.health().healthy is True

    # Redaction on facts & lessons via LocalMemoryProvider
    local_provider = LocalMemoryProvider()
    gw = MemoryGateway(provider=local_provider, redactor=Redactor())

    fact = _make_fact(
        fact_id="f-redact",
        summary="Contact alice@example.com with card 1234-5678-9012-3456",
    )
    lesson = _make_lesson(
        lesson_id="l-redact",
        title="Notify bob@example.com on failure",
        recommendation="Do not log 1234-5678-9012-3456 in errors",
    )

    assert gw.publish_fact(fact) == "f-redact"
    assert gw.publish_lesson(lesson) == "l-redact"

    stored_fact = local_provider.list_facts()[0]
    assert "alice@example.com" not in stored_fact.summary
    assert "1234-5678-9012-3456" not in stored_fact.summary
    assert "[REDACTED]" in stored_fact.summary

    stored_lesson = local_provider.list_lessons()[0]
    assert "bob@example.com" not in stored_lesson.title
    assert "1234-5678-9012-3456" not in stored_lesson.recommendation
    assert "[REDACTED]" in stored_lesson.title
    assert "[REDACTED]" in stored_lesson.recommendation

    promoted = gw.promote_lesson("l-redact", LessonReviewStatus.APPROVED, "sec-reviewer")
    assert promoted.is_policy_eligible is True

    # Graceful degradation when provider raises ConnectionError (fail_open=True)
    degraded_gw = MemoryGateway(provider=_OfflineProvider(), fail_open=True)
    assert degraded_gw.publish_fact(_make_fact()) is None
    assert degraded_gw.publish_lesson(_make_lesson()) is None
    assert degraded_gw.health().healthy is False
    degraded_ctx = degraded_gw.retrieve_advisory_context(MemoryQuery(query_text="auth"))
    assert degraded_ctx.degraded is True
    assert "memory backend offline" in (degraded_ctx.degraded_reason or "")
    assert degraded_ctx.facts == []
    assert degraded_ctx.approved_lessons == []

    # fail_open=False propagates ConnectionError
    strict_gw = MemoryGateway(provider=_OfflineProvider(), fail_open=False)
    with pytest.raises(ConnectionError):
        strict_gw.retrieve_advisory_context(MemoryQuery(query_text="auth"))

    # Authority violations are NEVER swallowed by fail_open=True
    smuggling_gw = MemoryGateway(
        provider=_SmugglingProvider(
            AdvisoryContext(
                provider_name="smuggler",
                approved_lessons=[_make_lesson(review_status=LessonReviewStatus.PROPOSED)],
            )
        ),
        fail_open=True,
    )
    with pytest.raises(MemoryAuthorityViolationError):
        smuggling_gw.retrieve_advisory_context(MemoryQuery(query_text="auth"))

    # Legacy LocalMemoryStore backward compatibility
    legacy_store = LocalMemoryStore()
    legacy_gw = MemoryGateway(store=legacy_store, redactor=Redactor())
    scope = MemoryScope(owner="agent-1", purpose="retro")
    entry = MemoryEntry(id="legacy-1", scope=scope, content="Email test@example.com")
    legacy_gw.store_memory(entry)
    retrieved = legacy_gw.get_memory("legacy-1", scope)
    assert retrieved is not None
    assert "test@example.com" not in retrieved.content
    assert "[REDACTED]" in retrieved.content
    assert len(legacy_gw.get_by_scope(scope)) == 1
