from enum import Enum
import re
from typing import List

from pydantic import BaseModel, Field

from vad.contracts.models import (
    AutonomyTier,
    Constraints,
    EIP,
    Goal,
    Invariants,
    MemoryRequirement,
    ModelBudget,
    ProofObligation,
    ReleaseRequirements,
    RiskTier,
    TelemetryRequirements,
    ToolPermissions,
    default_assurance_for_risk,
    default_freshness_for_risk,
)
from vad.memory.contracts import (
    AdvisoryContext,
    FactType,
    InferredLesson,
    MemoryQuery,
    ObservedFact,
    ProposedRuleType,
)
from vad.memory.gateway import MemoryGateway


class EffortType(str, Enum):
    BUGFIX = "bugfix"
    FEATURE = "feature"
    REFACTOR = "refactor"
    GREENFIELD = "greenfield"
    TEST = "test"
    MIGRATION = "migration"


class MeesBudget(BaseModel):
    minimum_score: int = Field(default=70, ge=0, le=100)
    max_changed_files: int = Field(default=5, ge=1)
    allow_new_dependencies: bool = False
    requires_justification: bool = False


class AskAssessment(BaseModel):
    summary: str
    ambiguity_score: int = Field(ge=0, le=100)
    risk_tier: RiskTier
    autonomy_tier: AutonomyTier
    effort_type: EffortType
    required_proof_kinds: List[str]
    tool_needs: List[str]
    memory_needs: List[str]
    model_tier: str
    budget: float = Field(ge=0)
    token_budget: int = Field(ge=0)
    loop_depth: int = Field(ge=1)
    release_needed: bool
    telemetry_needed: bool
    clarification_questions: List[str]
    mees_budget: MeesBudget
    retrieved_fact_ids: List[str] = Field(default_factory=list)
    applied_lesson_ids: List[str] = Field(default_factory=list)
    advisory_notes: List[str] = Field(default_factory=list)

    @property
    def blocks_autonomous_execution(self) -> bool:
        return bool(self.clarification_questions) or self.autonomy_tier == AutonomyTier.MANUAL


_RISK_ESCALATION_STEP = {
    RiskTier.LOW: RiskTier.MEDIUM,
    RiskTier.MEDIUM: RiskTier.HIGH,
    RiskTier.HIGH: RiskTier.CRITICAL,
    RiskTier.CRITICAL: RiskTier.CRITICAL,
}

_SECURITY_KEYWORDS = ("security", "auth", "token", "privacy", "credential")


def _sanitize_lesson_id(lesson_id: str) -> str:
    sanitized = re.sub(r"[^a-zA-Z0-9_-]+", "-", lesson_id.strip()).strip("-")
    return sanitized or "unknown"


def _is_security_lesson(lesson: InferredLesson) -> bool:
    lowered = lesson.recommendation.lower()
    return any(keyword in lowered for keyword in _SECURITY_KEYWORDS)


def _fact_matches_domain(fact: ObservedFact, domain_tags: List[str] | None) -> bool:
    if not domain_tags:
        return True
    requested = {tag.strip().lower() for tag in domain_tags if tag and tag.strip()}
    if not requested:
        return True
    fact_tags = {tag.strip().lower() for tag in fact.domain_tags if tag and tag.strip()}
    return bool(requested & fact_tags)


def _apply_advisory_context_to_assessment(
    assessment: AskAssessment,
    context: AdvisoryContext,
    *,
    domain_tags: List[str] | None = None,
) -> AskAssessment:
    fact_ids = list(assessment.retrieved_fact_ids)
    lesson_ids = list(assessment.applied_lesson_ids)
    notes = list(assessment.advisory_notes)
    proof_kinds = list(assessment.required_proof_kinds)
    risk_tier = assessment.risk_tier
    risk_escalated = False

    for fact in context.facts:
        if fact.fact_type not in (FactType.INCIDENT, FactType.VERIFICATION_FAILURE):
            continue
        if not _fact_matches_domain(fact, domain_tags):
            continue
        if fact.fact_id not in fact_ids:
            fact_ids.append(fact.fact_id)
        note = f"Fact {fact.fact_id} ({fact.fact_type.value}): {fact.summary}"
        if note not in notes:
            notes.append(note)

    # Defense-in-depth: strictly ignore any lesson not marked policy-eligible
    # and never inspect context.advisory_unapproved_lessons for policy/risk/proofs.
    eligible_lessons = [
        lesson for lesson in context.approved_lessons if lesson.is_policy_eligible
    ]

    for lesson in eligible_lessons:
        if lesson.proposed_rule_type == ProposedRuleType.RISK_CALIBRATION:
            if not risk_escalated:
                risk_tier = _RISK_ESCALATION_STEP[risk_tier]
                risk_escalated = True
            if lesson.lesson_id not in lesson_ids:
                lesson_ids.append(lesson.lesson_id)
            note = f"Lesson {lesson.lesson_id} (risk_calibration): {lesson.recommendation}"
            if note not in notes:
                notes.append(note)
        elif lesson.proposed_rule_type == ProposedRuleType.PROOF_OBLIGATION:
            proof_kind = "security" if _is_security_lesson(lesson) else "unit"
            if proof_kind not in proof_kinds:
                proof_kinds.append(proof_kind)
            if lesson.lesson_id not in lesson_ids:
                lesson_ids.append(lesson.lesson_id)
            note = f"Lesson {lesson.lesson_id} (proof_obligation): {lesson.recommendation}"
            if note not in notes:
                notes.append(note)
        elif lesson.proposed_rule_type == ProposedRuleType.INVARIANT:
            if lesson.lesson_id not in lesson_ids:
                lesson_ids.append(lesson.lesson_id)
            note = f"Lesson {lesson.lesson_id} (invariant): {lesson.recommendation}"
            if note not in notes:
                notes.append(note)
        else:
            note = f"Lesson {lesson.lesson_id} ({lesson.proposed_rule_type.value}): {lesson.recommendation}"
            if note not in notes:
                notes.append(note)

    return assessment.model_copy(
        update={
            "risk_tier": risk_tier,
            "required_proof_kinds": proof_kinds,
            "retrieved_fact_ids": fact_ids,
            "applied_lesson_ids": lesson_ids,
            "advisory_notes": notes,
        }
    )


def enrich_assessment_with_memory(
    assessment: AskAssessment,
    *,
    memory_gateway: MemoryGateway | None,
    domain_tags: List[str] | None = None,
) -> AskAssessment:
    """Enrich an AskAssessment using retrieved facts and approved lessons only."""
    if memory_gateway is None:
        return assessment

    context = memory_gateway.retrieve_advisory_context(
        MemoryQuery(
            query_text=assessment.summary,
            domain_tags=domain_tags or [],
            include_unapproved_lessons=False,
        )
    )
    return _apply_advisory_context_to_assessment(
        assessment,
        context,
        domain_tags=domain_tags,
    )


def eip_from_assessment(
    name: str,
    assessment: AskAssessment,
    *,
    memory_gateway: MemoryGateway | None = None,
    domain_tags: List[str] | None = None,
) -> EIP:
    effective_assessment = assessment
    eligible_lessons: List[InferredLesson] = []
    if memory_gateway is not None:
        context = memory_gateway.retrieve_advisory_context(
            MemoryQuery(
                query_text=assessment.summary,
                domain_tags=domain_tags or [],
                include_unapproved_lessons=False,
            )
        )
        effective_assessment = _apply_advisory_context_to_assessment(
            assessment,
            context,
            domain_tags=domain_tags,
        )
        eligible_lessons = [
            lesson for lesson in context.approved_lessons if lesson.is_policy_eligible
        ]

    proof_obligations = [
        ProofObligation(
            id=f"po-{index}",
            kind=kind,
            description=f"Provide {kind} proof for: {effective_assessment.summary}",
        )
        for index, kind in enumerate(effective_assessment.required_proof_kinds, start=1)
    ]
    if effective_assessment.autonomy_tier == AutonomyTier.MANUAL:
        proof_obligations.append(
            ProofObligation(
                id=f"po-{len(proof_obligations) + 1}",
                kind="manual_gate",
                description="Human approval required before autonomous implementation.",
            )
        )

    functional_invariants = ["Implementation satisfies the assessed ask."]
    security_invariants: List[str] = []

    if memory_gateway is not None:
        existing_po_ids = {ob.id for ob in proof_obligations}
        is_medium_or_higher = effective_assessment.risk_tier in (
            RiskTier.MEDIUM,
            RiskTier.HIGH,
            RiskTier.CRITICAL,
        )
        for lesson in eligible_lessons:
            if lesson.proposed_rule_type == ProposedRuleType.INVARIANT:
                target_list = (
                    security_invariants
                    if _is_security_lesson(lesson)
                    else functional_invariants
                )
                if lesson.recommendation not in target_list:
                    target_list.append(lesson.recommendation)
            elif lesson.proposed_rule_type == ProposedRuleType.PROOF_OBLIGATION:
                ob_id = f"po-lesson-{_sanitize_lesson_id(lesson.lesson_id)}"
                if ob_id in existing_po_ids:
                    continue
                kind = "security" if _is_security_lesson(lesson) else "unit"
                proof_obligations.append(
                    ProofObligation(
                        id=ob_id,
                        kind=kind,
                        description=lesson.recommendation or lesson.title,
                        independent_execution_required=is_medium_or_higher,
                    )
                )
                existing_po_ids.add(ob_id)

    return EIP(
        version="1.0.0",
        name=name,
        goal=Goal(description=effective_assessment.summary, success_criteria=["All proof obligations pass."]),
        non_goals=[],
        risk_tier=effective_assessment.risk_tier,
        autonomy_tier=effective_assessment.autonomy_tier,
        scope_boundaries=[],
        invariants=Invariants(
            functional=functional_invariants,
            security=security_invariants,
        ),
        constraints=Constraints(operational=effective_assessment.clarification_questions),
        proof_obligations=proof_obligations,
        tool_permissions=ToolPermissions(allowed=effective_assessment.tool_needs, denied=["network"]),
        memory_requirements=[
            MemoryRequirement(scope=scope, purpose="ask assessment context")
            for scope in effective_assessment.memory_needs
        ],
        model_budget=ModelBudget(
            max_tokens=effective_assessment.token_budget,
            max_cost=effective_assessment.budget,
            max_loop_depth=effective_assessment.loop_depth,
        ),
        release_requirements=ReleaseRequirements(
            required=effective_assessment.release_needed,
            strategy="gated" if effective_assessment.release_needed else None,
            gates=["health"] if effective_assessment.release_needed else [],
        ),
        telemetry_requirements=TelemetryRequirements(
            required=effective_assessment.telemetry_needed,
            signals=["traces", "metrics"] if effective_assessment.telemetry_needed else [],
        ),
        assurance_level=(
            default_assurance_for_risk(effective_assessment.risk_tier)
            if memory_gateway is not None
            else None
        ),
        freshness_policy=(
            default_freshness_for_risk(effective_assessment.risk_tier)
            if memory_gateway is not None
            else None
        ),
    )
