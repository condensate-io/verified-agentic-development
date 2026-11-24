from __future__ import annotations

from datetime import datetime, timezone

from vad.ask.assessment import (
    AskAssessment,
    eip_from_assessment,
    enrich_assessment_with_memory,
)
from vad.ask.classifier import assess_ask
from vad.contracts.models import (
    AssuranceLevel,
    ProofKind,
    RiskTier,
    default_assurance_for_risk,
    default_freshness_for_risk,
)
from vad.memory.contracts import (
    AdvisoryContext,
    FactType,
    InferredLesson,
    LessonReviewStatus,
    MemoryQuery,
    ObservedFact,
    ProposedRuleType,
)
from vad.memory.gateway import MemoryGateway
from vad.memory.providers.local import LocalMemoryProvider
from vad.memory.providers.null import NullMemoryProvider
from vad.proof.mapper import enrich_eip_proofs_from_memory, map_proofs
from vad.proof.plan import compute_eip_digest

_DIGEST_A = "a" * 64


class _OfflineFailingProvider:
    name = "offline-stub"

    def capabilities(self):
        raise ConnectionError("http://127.0.0.1:9999 unreachable")

    def health(self):
        raise ConnectionError("http://127.0.0.1:9999 unreachable")

    def record_fact(self, fact):
        raise ConnectionError("http://127.0.0.1:9999 unreachable")

    def record_lesson(self, lesson):
        raise ConnectionError("http://127.0.0.1:9999 unreachable")

    def update_lesson_status(self, lesson_id, status, reviewed_by, promotion_record_id=None):
        raise ConnectionError("http://127.0.0.1:9999 unreachable")

    def query(self, query):
        raise ConnectionError("http://127.0.0.1:9999 unreachable")

    def list_facts(self, limit=50):
        return []

    def list_lessons(self, status=None, limit=50):
        return []


class _UnapprovedLeakProvider(NullMemoryProvider):
    name = "unapproved-leak-stub"

    def __init__(self, leaked_lessons: list[InferredLesson]) -> None:
        self._leaked = leaked_lessons

    def query(self, query: MemoryQuery) -> AdvisoryContext:
        return AdvisoryContext(
            provider_name=self.name,
            facts=[],
            approved_lessons=[],
            advisory_unapproved_lessons=list(self._leaked),
        )


def _make_fact(
    fact_id: str,
    fact_type: FactType,
    *,
    domain_tags: list[str],
    summary: str,
) -> ObservedFact:
    return ObservedFact(
        fact_id=fact_id,
        fact_type=fact_type,
        run_id="run-1",
        eip_digest="b" * 64,
        source_evidence_digest=_DIGEST_A,
        risk_tier="medium",
        domain_tags=domain_tags,
        summary=summary,
    )


def _make_lesson(
    lesson_id: str,
    *,
    rule_type: ProposedRuleType,
    recommendation: str,
    domain_tags: list[str],
    status: LessonReviewStatus = LessonReviewStatus.PROPOSED,
    reviewed_by: str | None = None,
) -> InferredLesson:
    return InferredLesson(
        lesson_id=lesson_id,
        title=f"Lesson {lesson_id}",
        recommendation=recommendation,
        proposed_rule_type=rule_type,
        derived_from_fact_ids=["fact-1"],
        domain_tags=domain_tags,
        confidence=0.9,
        review_status=status,
        reviewed_by=reviewed_by,
        reviewed_at=datetime.now(timezone.utc) if reviewed_by else None,
    )


def test_null_and_offline_degraded_providers_leave_assessment_and_proofs_intact():
    base_assessment = assess_ask("Add a CLI flag in vad/cli.py so that output can be JSON.")
    base_eip = eip_from_assessment("json-flag", base_assessment)
    base_plan = map_proofs(base_eip)

    for gateway in (
        MemoryGateway(provider=NullMemoryProvider()),
        MemoryGateway(provider=_OfflineFailingProvider(), fail_open=True),
    ):
        enriched_assessment = enrich_assessment_with_memory(
            base_assessment,
            memory_gateway=gateway,
            domain_tags=["cli"],
        )
        assert enriched_assessment == base_assessment
        assert enriched_assessment.retrieved_fact_ids == []
        assert enriched_assessment.applied_lesson_ids == []
        assert enriched_assessment.advisory_notes == []

        enriched_eip, applied = enrich_eip_proofs_from_memory(
            base_eip,
            gateway,
            domain_tags=["cli"],
        )
        assert enriched_eip == base_eip
        assert applied == []

        plan = map_proofs(base_eip, memory_gateway=gateway, domain_tags=["cli"])
        assert plan == base_plan
        assert plan.eip_digest == compute_eip_digest(base_eip)


def test_none_gateway_preserves_backward_compatibility():
    assessment = assess_ask("Add a CLI flag in vad/cli.py so that output can be JSON.")
    assert enrich_assessment_with_memory(assessment, memory_gateway=None) is assessment

    eip = eip_from_assessment("json-flag", assessment, memory_gateway=None)
    assert eip.assurance_level is None
    assert eip.freshness_policy is None

    unchanged_eip, applied = enrich_eip_proofs_from_memory(eip, None)
    assert unchanged_eip is eip
    assert applied == []


def test_unapproved_proposed_and_rejected_lessons_are_strictly_ignored():
    provider = LocalMemoryProvider()
    proposed_risk = _make_lesson(
        "proposed-risk",
        rule_type=ProposedRuleType.RISK_CALIBRATION,
        recommendation="Escalate risk for CLI parser",
        domain_tags=["cli"],
        status=LessonReviewStatus.PROPOSED,
    )
    rejected_proof = _make_lesson(
        "rejected-proof",
        rule_type=ProposedRuleType.PROOF_OBLIGATION,
        recommendation="Add security token check",
        domain_tags=["cli"],
        status=LessonReviewStatus.REJECTED,
        reviewed_by="reviewer-1",
    )
    provider.record_lesson(proposed_risk)
    provider.record_lesson(rejected_proof)

    gateway = MemoryGateway(provider=provider)
    base_assessment = assess_ask("Add a CLI flag in vad/cli.py so that output can be JSON.")
    enriched_assessment = enrich_assessment_with_memory(
        base_assessment,
        memory_gateway=gateway,
        domain_tags=["cli"],
    )

    assert enriched_assessment.risk_tier == RiskTier.LOW
    assert enriched_assessment.required_proof_kinds == base_assessment.required_proof_kinds
    assert enriched_assessment.applied_lesson_ids == []

    base_eip = eip_from_assessment("json-flag", base_assessment)
    enriched_eip, applied = enrich_eip_proofs_from_memory(
        base_eip,
        gateway,
        domain_tags=["cli"],
    )
    assert enriched_eip == base_eip
    assert applied == []

    # Defense-in-depth: even if a provider leaks unapproved lessons in advisory_unapproved_lessons
    leak_gateway = MemoryGateway(provider=_UnapprovedLeakProvider([proposed_risk, rejected_proof]))
    assert enrich_assessment_with_memory(
        base_assessment,
        memory_gateway=leak_gateway,
        domain_tags=["cli"],
    ) == base_assessment
    leaked_eip, leaked_applied = enrich_eip_proofs_from_memory(
        base_eip,
        leak_gateway,
        domain_tags=["cli"],
    )
    assert leaked_eip == base_eip
    assert leaked_applied == []


def test_facts_and_approved_lessons_enrich_assessment_eip_and_proof_plan():
    provider = LocalMemoryProvider()
    provider.record_fact(
        _make_fact(
            "fact-inc-1",
            FactType.INCIDENT,
            domain_tags=["auth"],
            summary="Expired session token caused unauthorized API access",
        )
    )
    provider.record_fact(
        _make_fact(
            "fact-vf-2",
            FactType.VERIFICATION_FAILURE,
            domain_tags=["auth"],
            summary="Missing negative test for malformed JWT",
        )
    )
    # Different domain fact should be ignored when domain_tags=["auth"]
    provider.record_fact(
        _make_fact(
            "fact-other",
            FactType.INCIDENT,
            domain_tags=["billing"],
            summary="Invoice rounding error",
        )
    )

    provider.record_lesson(
        _make_lesson(
            "risk-up",
            rule_type=ProposedRuleType.RISK_CALIBRATION,
            recommendation="Treat token parser changes as higher risk",
            domain_tags=["auth"],
            status=LessonReviewStatus.APPROVED,
            reviewed_by="sec-lead",
        )
    )
    provider.record_lesson(
        _make_lesson(
            "proof-sec/01",
            rule_type=ProposedRuleType.PROOF_OBLIGATION,
            recommendation="Verify auth token expiry rejection in regression suite",
            domain_tags=["auth"],
            status=LessonReviewStatus.APPROVED,
            reviewed_by="sec-lead",
        )
    )
    provider.record_lesson(
        _make_lesson(
            "proof-unit-02",
            rule_type=ProposedRuleType.PROOF_OBLIGATION,
            recommendation="Check edge-case parser boundary handling",
            domain_tags=["auth"],
            status=LessonReviewStatus.APPROVED,
            reviewed_by="eng-lead",
        )
    )
    provider.record_lesson(
        _make_lesson(
            "inv-sec-01",
            rule_type=ProposedRuleType.INVARIANT,
            recommendation="Never accept expired auth tokens",
            domain_tags=["auth"],
            status=LessonReviewStatus.APPROVED,
            reviewed_by="sec-lead",
        )
    )
    provider.record_lesson(
        _make_lesson(
            "inv-func-02",
            rule_type=ProposedRuleType.INVARIANT,
            recommendation="Parser must preserve input ordering",
            domain_tags=["auth"],
            status=LessonReviewStatus.APPROVED,
            reviewed_by="eng-lead",
        )
    )

    gateway = MemoryGateway(provider=provider)
    low_assessment = assess_ask("Add helper function in parser module so that lines are trimmed.")
    assert low_assessment.risk_tier == RiskTier.LOW

    enriched = enrich_assessment_with_memory(
        low_assessment,
        memory_gateway=gateway,
        domain_tags=["auth"],
    )
    assert enriched.risk_tier == RiskTier.MEDIUM
    assert "security" in enriched.required_proof_kinds
    assert "unit" in enriched.required_proof_kinds
    assert enriched.retrieved_fact_ids == ["fact-inc-1", "fact-vf-2"]
    assert set(enriched.applied_lesson_ids) == {
        "risk-up",
        "proof-sec/01",
        "proof-unit-02",
        "inv-sec-01",
        "inv-func-02",
    }
    assert any("fact-inc-1" in note for note in enriched.advisory_notes)
    assert any("fact-vf-2" in note for note in enriched.advisory_notes)

    eip = eip_from_assessment(
        "parser-helper",
        low_assessment,
        memory_gateway=gateway,
        domain_tags=["auth"],
    )
    assert eip.risk_tier == RiskTier.MEDIUM
    assert eip.assurance_level == default_assurance_for_risk(RiskTier.MEDIUM)
    assert eip.freshness_policy == default_freshness_for_risk(RiskTier.MEDIUM)
    assert "Never accept expired auth tokens" in eip.invariants.security
    assert "Parser must preserve input ordering" in eip.invariants.functional

    lesson_obs = {ob.id: ob for ob in eip.proof_obligations if ob.id.startswith("po-lesson-")}
    assert "po-lesson-proof-sec-01" in lesson_obs
    assert lesson_obs["po-lesson-proof-sec-01"].kind == ProofKind.SECURITY
    assert lesson_obs["po-lesson-proof-sec-01"].independent_execution_required is True
    assert "po-lesson-proof-unit-02" in lesson_obs
    assert lesson_obs["po-lesson-proof-unit-02"].kind == ProofKind.UNIT
    assert lesson_obs["po-lesson-proof-unit-02"].independent_execution_required is True

    # Calling map_proofs with gateway avoids duplicate po-lesson-* obligations and keeps digest exact
    plan = map_proofs(eip, memory_gateway=gateway, domain_tags=["auth"])
    assert plan.eip_digest == compute_eip_digest(eip)
    assert plan.covers_obligation("po-lesson-proof-sec-01")
    assert plan.covers_obligation("po-lesson-proof-unit-02")


def test_risk_calibration_escalates_and_never_downgrades_and_mapper_enriches_raw_eip():
    provider = LocalMemoryProvider()
    provider.record_lesson(
        _make_lesson(
            "risk-cal-downgrade-attempt",
            rule_type=ProposedRuleType.RISK_CALIBRATION,
            recommendation="Downgrade to low risk because tests exist",
            domain_tags=["api"],
            status=LessonReviewStatus.APPROVED,
            reviewed_by="lead-1",
        )
    )
    provider.record_lesson(
        _make_lesson(
            "reg-api-1",
            rule_type=ProposedRuleType.PROOF_OBLIGATION,
            recommendation="Run regression check for API auth header",
            domain_tags=["api"],
            status=LessonReviewStatus.APPROVED,
            reviewed_by="lead-1",
        )
    )
    gateway = MemoryGateway(provider=provider)

    medium_assessment = assess_ask("Add a new API integration in service module so that data sync succeeds.")
    assert medium_assessment.risk_tier == RiskTier.MEDIUM

    escalated = enrich_assessment_with_memory(
        medium_assessment,
        memory_gateway=gateway,
        domain_tags=["api"],
    )
    assert escalated.risk_tier == RiskTier.HIGH

    high_assessment = assess_ask("Fix the auth bypass in production so that private data is protected.")
    assert high_assessment.risk_tier == RiskTier.HIGH
    critical_escalated = enrich_assessment_with_memory(
        high_assessment,
        memory_gateway=gateway,
        domain_tags=["api"],
    )
    assert critical_escalated.risk_tier == RiskTier.CRITICAL

    # Verify enrich_eip_proofs_from_memory + map_proofs on a raw EIP without prior memory enrichment
    raw_eip = eip_from_assessment("api-sync", medium_assessment)
    enriched_eip, applied_ids = enrich_eip_proofs_from_memory(
        raw_eip,
        gateway,
        domain_tags=["api"],
    )
    assert applied_ids == ["reg-api-1"]
    added_ob = next(ob for ob in enriched_eip.proof_obligations if ob.id == "po-lesson-reg-api-1")
    assert added_ob.kind == ProofKind.SECURITY
    assert added_ob.independent_execution_required is True

    plan = map_proofs(raw_eip, memory_gateway=gateway, domain_tags=["api"])
    assert plan.eip_digest == compute_eip_digest(enriched_eip)
    assert plan.covers_obligation("po-lesson-reg-api-1")
    sec_mapping = plan.get_mapping("po-lesson-reg-api-1")
    assert sec_mapping is not None
    assert sec_mapping.test_command == "pytest -m security"
