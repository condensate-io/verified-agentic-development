import pytest

from vad.evidence.bundle import EvidenceBundle
from vad.feedback.lesson_review import LessonReviewService
from vad.feedback.publisher import LearningLoopPublisher
from vad.feedback.retro import RetroAnalyzer
from vad.memory.authority_guard import MemoryAuthorityViolationError
from vad.memory.contracts import (
    FactType,
    InferredLesson,
    LessonReviewStatus,
    MemoryQuery,
    MemoryScope,
    ObservedFact,
    ProposedRuleType,
)
from vad.memory.gateway import MemoryGateway
from vad.memory.providers.local import LocalMemoryProvider
from vad.memory.providers.null import NullMemoryProvider
from vad.memory.redaction import Redactor
from vad.memory.stores.local import LocalMemoryStore
from vad.server.db.store import ServerStore


def _digest(char: str = "a") -> str:
    return char * 64


def test_learning_loop_publisher_separates_observed_facts_from_proposed_lessons(tmp_path):
    provider = LocalMemoryProvider()
    gateway = MemoryGateway(provider=provider, redactor=Redactor())
    store = ServerStore(tmp_path / "vad.sqlite3")
    publisher = LearningLoopPublisher(memory_gateway=gateway, store=store)

    fact = publisher.publish_run_outcome(
        run_id="run-001",
        eip_digest=_digest("1"),
        evidence_digest=_digest("a"),
        risk_tier="high",
        fact_type=FactType.VERIFICATION_FAILURE,
        summary="Verification failed for owner alice@example.com on migration step.",
        domain_tags=["sqlite", "migrations"],
        metadata={"step": "verify"},
    )

    assert isinstance(fact, ObservedFact)
    assert fact.authoritative_source == "vad"
    assert fact.source_evidence_digest == _digest("a")
    assert "alice@example.com" not in fact.summary
    assert "[REDACTED]" in fact.summary
    assert len(provider.list_facts()) == 1
    assert len(provider.list_lessons()) == 0

    lesson = publisher.publish_candidate_lesson(
        title="Require migration check for bob@example.com",
        recommendation="Add idempotency check before ALTER TABLE.",
        proposed_rule_type=ProposedRuleType.PROOF_OBLIGATION,
        derived_from_fact_ids=[fact.fact_id],
        domain_tags=["sqlite", "migrations"],
        confidence=0.75,
    )

    assert isinstance(lesson, InferredLesson)
    assert lesson.review_status == LessonReviewStatus.PROPOSED
    assert lesson.reviewed_by is None
    assert lesson.is_policy_eligible is False
    assert lesson.derived_from_fact_ids == [fact.fact_id]
    assert "bob@example.com" not in lesson.title
    assert "[REDACTED]" in lesson.title

    # Provider stores fact and lesson separately, and lesson is NOT approved
    context_without_unapproved = gateway.retrieve_advisory_context(
        MemoryQuery(query_text="migration", include_unapproved_lessons=False)
    )
    assert len(context_without_unapproved.facts) == 1
    assert context_without_unapproved.approved_lessons == []
    assert context_without_unapproved.advisory_unapproved_lessons == []

    # Authoritative SQLite store has the proposed record owned by VAD
    stored_record = store.load_lesson_promotion(lesson.lesson_id)
    assert stored_record is not None
    assert stored_record.review_status == "proposed"
    assert stored_record.reviewed_by is None
    assert stored_record.promotion_id == lesson.promotion_record_id


def test_publisher_rejects_invalid_digest_and_forbidden_authority_metadata():
    gateway = MemoryGateway(provider=LocalMemoryProvider())
    publisher = LearningLoopPublisher(memory_gateway=gateway)

    with pytest.raises(ValueError, match="64-character hex SHA-256"):
        publisher.publish_run_outcome(
            run_id="run-bad-digest",
            eip_digest=_digest("1"),
            evidence_digest="not-a-sha256",
            risk_tier="low",
            fact_type=FactType.VERIFIED_DELIVERY,
            summary="Bad digest",
        )

    with pytest.raises(MemoryAuthorityViolationError):
        publisher.publish_run_outcome(
            run_id="run-bad-meta",
            eip_digest=_digest("1"),
            evidence_digest=_digest("b"),
            risk_tier="low",
            fact_type=FactType.VERIFIED_DELIVERY,
            summary="Attempt override",
            metadata={"approval_granted": True},
        )


def test_lesson_review_service_blocks_self_promotion_and_requires_human_approval(tmp_path):
    provider = LocalMemoryProvider()
    gateway = MemoryGateway(provider=provider)
    store = ServerStore(tmp_path / "vad.sqlite3")
    publisher = LearningLoopPublisher(memory_gateway=gateway, store=store)
    review_service = LessonReviewService(store=store, memory_gateway=gateway)

    fact = publisher.publish_run_outcome(
        run_id="run-auth-1",
        eip_digest=_digest("2"),
        evidence_digest=_digest("c"),
        risk_tier="high",
        fact_type=FactType.POLICY_DENIAL,
        summary="Policy denied shell tool usage.",
        domain_tags=["policy"],
    )
    lesson_1 = publisher.publish_candidate_lesson(
        title="Restrict shell tool in high-risk runs",
        recommendation="Deny unrestricted shell calls unless explicitly scoped.",
        proposed_rule_type=ProposedRuleType.TOOL_RESTRICTION,
        derived_from_fact_ids=[fact.fact_id],
        domain_tags=["policy"],
    )
    lesson_2 = publisher.publish_candidate_lesson(
        title="No-op speculative guidance",
        recommendation="Ignore transient warning.",
        proposed_rule_type=ProposedRuleType.GENERAL_GUIDANCE,
        derived_from_fact_ids=[fact.fact_id],
        domain_tags=["policy"],
    )

    # Pre-approved submission attempt must be rejected
    pre_approved = InferredLesson(
        lesson_id="lesson-pre-approved",
        title="Bypass review",
        recommendation="Auto approve myself",
        review_status=LessonReviewStatus.APPROVED,
        reviewed_by="builder-bot",
    )
    with pytest.raises(PermissionError):
        review_service.submit_for_review(pre_approved)

    # Builder and agent roles CANNOT approve or reject lessons
    for unauthorized_role in ("builder", "agent", "subagent"):
        with pytest.raises(PermissionError):
            review_service.approve_lesson(
                lesson_1.lesson_id,
                reviewer="autonomous-builder",
                reviewer_role=unauthorized_role,
            )
        with pytest.raises(PermissionError):
            review_service.reject_lesson(
                lesson_2.lesson_id,
                reviewer="autonomous-builder",
                reviewer_role=unauthorized_role,
            )

    # Empty reviewer must fail
    with pytest.raises(ValueError, match="reviewer"):
        review_service.approve_lesson(
            lesson_1.lesson_id,
            reviewer="   ",
            reviewer_role="release_guardian",
        )

    assert review_service.list_policy_eligible_lessons() == []

    # Release guardian approves lesson_1 -> updates SQLite AND syncs to LocalMemoryProvider
    approved_record = review_service.approve_lesson(
        lesson_1.lesson_id,
        reviewer="guardian-alice",
        reviewer_role="release_guardian",
        note="Verified against policy denial evidence",
    )
    assert approved_record.review_status == "approved"
    assert approved_record.reviewed_by == "guardian-alice"
    assert approved_record.promotion_id.startswith("promo-")
    assert approved_record.metadata["review_note"] == "Verified against policy denial evidence"

    # Operator rejects lesson_2 -> blocks policy eligibility
    rejected_record = review_service.reject_lesson(
        lesson_2.lesson_id,
        reviewer="operator-bob",
        reviewer_role="operator",
        reason="Too broad",
    )
    assert rejected_record.review_status == "rejected"
    assert rejected_record.reviewed_by == "operator-bob"

    # Authoritative queries return only approved lessons
    assert len(review_service.list_lessons(LessonReviewStatus.APPROVED)) == 1
    assert len(review_service.list_lessons(LessonReviewStatus.REJECTED)) == 1
    assert review_service.list_lessons(LessonReviewStatus.PROPOSED) == []

    eligible = review_service.list_policy_eligible_lessons()
    assert len(eligible) == 1
    assert eligible[0].lesson_id == lesson_1.lesson_id
    assert eligible[0].review_status == LessonReviewStatus.APPROVED
    assert eligible[0].reviewed_by == "guardian-alice"
    assert eligible[0].is_policy_eligible is True

    # LocalMemoryProvider is synced
    context = gateway.retrieve_advisory_context(
        MemoryQuery(query_text="shell", include_unapproved_lessons=True)
    )
    assert [lesson.lesson_id for lesson in context.approved_lessons] == [lesson_1.lesson_id]
    assert context.approved_lessons[0].promotion_record_id == approved_record.promotion_id


def test_lesson_review_service_ignores_offline_memory_provider_errors(tmp_path):
    class OfflineProvider(NullMemoryProvider):
        def update_lesson_status(self, lesson_id, status, reviewed_by, promotion_record_id=None):
            raise ConnectionError("memory backend offline at http://127.0.0.1:9999")

    store = ServerStore(tmp_path / "vad.sqlite3")
    gateway = MemoryGateway(provider=OfflineProvider())
    review_service = LessonReviewService(store=store, memory_gateway=gateway)

    proposed = InferredLesson(
        lesson_id="lesson-offline-1",
        title="Add retry invariant",
        recommendation="Bound retry backoff.",
        proposed_rule_type=ProposedRuleType.INVARIANT,
        derived_from_fact_ids=["fact-1"],
        review_status=LessonReviewStatus.PROPOSED,
    )
    submitted = review_service.submit_for_review(proposed)
    assert submitted.review_status == "proposed"

    # Approval still succeeds authoritatively in SQLite despite offline memory provider
    approved = review_service.approve_lesson(
        "lesson-offline-1",
        reviewer="operator-carol",
        reviewer_role="operator",
    )
    assert approved.review_status == "approved"
    assert approved.reviewed_by == "operator-carol"
    eligible = review_service.list_policy_eligible_lessons()
    assert [lesson.lesson_id for lesson in eligible] == ["lesson-offline-1"]


def test_retro_analyzer_learning_loop_separates_facts_and_lessons_and_preserves_legacy(tmp_path):
    # 1. Legacy RetroAnalyzer.analyze remains 100% backward-compatible
    legacy_store = LocalMemoryStore()
    legacy_gateway = MemoryGateway(legacy_store, Redactor())
    legacy_analyzer = RetroAnalyzer(legacy_gateway)
    legacy_bundle = EvidenceBundle({"failures": ["retry timeout", "retry timeout"]})
    legacy_result = legacy_analyzer.analyze(legacy_bundle)

    assert len(legacy_result["failures"]) == 2
    assert len(legacy_store.get_by_scope(MemoryScope.RETROSPECTIVE)) == 1
    assert {p["proposal_type"] for p in legacy_result["proposals"]} == {
        "add_proof_obligation",
        "add_invariant",
    }

    # 2. New analyze_for_learning_loop separates ObservedFact from PROPOSED InferredLessons
    provider = LocalMemoryProvider()
    v1_gateway = MemoryGateway(provider=provider, redactor=Redactor())
    sqlite_store = ServerStore(tmp_path / "vad.sqlite3")
    analyzer = RetroAnalyzer(v1_gateway)

    bundle = EvidenceBundle({
        "failures": ["Policy denied shell execution", "Policy denied shell execution"],
        "steps": [
            {"status": "failure", "loop_exhaustion": "Exhausted 5 loops"},
        ],
    })
    loop_result = analyzer.analyze_for_learning_loop(
        bundle,
        run_id="run-loop-1",
        eip_digest=_digest("3"),
        risk_tier="medium",
        domain_tags=["shell", "budget"],
        store=sqlite_store,
    )

    fact = loop_result["observed_fact"]
    lessons = loop_result["candidate_lessons"]

    assert isinstance(fact, ObservedFact)
    assert fact.run_id == "run-loop-1"
    assert fact.fact_type == FactType.POLICY_DENIAL
    assert fact.source_evidence_digest == bundle.compute_hash()
    assert len(lessons) >= 3
    for lesson in lessons:
        assert isinstance(lesson, InferredLesson)
        assert lesson.review_status == LessonReviewStatus.PROPOSED
        assert lesson.reviewed_by is None
        assert lesson.is_policy_eligible is False
        assert lesson.derived_from_fact_ids == [fact.fact_id]

    review_service = LessonReviewService(store=sqlite_store, memory_gateway=v1_gateway)
    assert review_service.list_policy_eligible_lessons() == []
    assert len(review_service.list_lessons(LessonReviewStatus.PROPOSED)) == len(lessons)

    # Also verify clean operation with NullMemoryProvider
    null_analyzer = RetroAnalyzer(MemoryGateway(provider=NullMemoryProvider()))
    clean_bundle = EvidenceBundle({"final_decision": "accepted", "steps": [{"status": "success"}]})
    clean_result = null_analyzer.analyze_for_learning_loop(
        clean_bundle,
        run_id="run-clean-1",
        eip_digest=_digest("4"),
        risk_tier="low",
    )
    assert clean_result["observed_fact"].fact_type == FactType.VERIFIED_DELIVERY
    assert clean_result["candidate_lessons"] == []
