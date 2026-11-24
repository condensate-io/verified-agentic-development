import re

from vad.contracts.models import EIP, ProofKind, ProofObligation
from vad.memory.contracts import MemoryQuery, ProposedRuleType
from vad.memory.gateway import MemoryGateway
from vad.proof.plan import ProofMapping, ProofPlan, compute_eip_digest

_SECURITY_KEYWORDS = ("security", "auth", "token")


def _sanitize_lesson_id(lesson_id: str) -> str:
    sanitized = re.sub(r"[^a-zA-Z0-9_-]+", "-", lesson_id.strip()).strip("-")
    return sanitized or "unknown"


def _is_security_recommendation(recommendation: str) -> bool:
    lowered = recommendation.lower()
    return any(keyword in lowered for keyword in _SECURITY_KEYWORDS)


COMMANDS_BY_KIND = {
    ProofKind.UNIT: "pytest",
    ProofKind.PROPERTY: "pytest",
    ProofKind.CONTRACT: "pytest -m contract",
    ProofKind.SECURITY: "pytest -m security",
    ProofKind.PERFORMANCE: "pytest -m performance",
    ProofKind.POLICY: "pytest -m policy",
    ProofKind.RELEASE: "pytest -m release",
    ProofKind.TELEMETRY: "pytest -m telemetry",
}


def enrich_eip_proofs_from_memory(
    eip: EIP,
    memory_gateway: MemoryGateway | None,
    *,
    domain_tags: list[str] | None = None,
) -> tuple[EIP, list[str]]:
    """Enrich an EIP with regression proof obligations from approved memory lessons."""
    if memory_gateway is None:
        return eip, []

    context = memory_gateway.retrieve_advisory_context(
        MemoryQuery(
            query_text=eip.goal.description,
            domain_tags=domain_tags or [],
            include_unapproved_lessons=False,
        )
    )
    eligible_lessons = [
        lesson
        for lesson in context.approved_lessons
        if lesson.is_policy_eligible
        and lesson.proposed_rule_type == ProposedRuleType.PROOF_OBLIGATION
    ]
    if not eligible_lessons:
        return eip, []

    existing_ids = {ob.id for ob in eip.proof_obligations}
    updated_obligations = list(eip.proof_obligations)
    applied_lesson_ids: list[str] = []

    for lesson in eligible_lessons:
        obligation_id = f"po-lesson-{_sanitize_lesson_id(lesson.lesson_id)}"
        if obligation_id in existing_ids:
            continue
        kind = (
            ProofKind.SECURITY
            if _is_security_recommendation(lesson.recommendation)
            else ProofKind.UNIT
        )
        updated_obligations.append(
            ProofObligation(
                id=obligation_id,
                kind=kind,
                description=lesson.recommendation or lesson.title,
                independent_execution_required=True,
            )
        )
        existing_ids.add(obligation_id)
        applied_lesson_ids.append(lesson.lesson_id)

    if not applied_lesson_ids:
        return eip, []

    return eip.model_copy(update={"proof_obligations": updated_obligations}), applied_lesson_ids


def map_proofs(
    eip: EIP,
    *,
    memory_gateway: MemoryGateway | None = None,
    domain_tags: list[str] | None = None,
) -> ProofPlan:
    if memory_gateway is not None:
        eip, _ = enrich_eip_proofs_from_memory(
            eip,
            memory_gateway,
            domain_tags=domain_tags,
        )
    _ensure_proof_coverage(eip)
    mappings = []
    manual_gates = []
    for obligation in sorted(eip.proof_obligations, key=lambda item: item.id):
        if obligation.kind == ProofKind.MANUAL_GATE:
            manual_gates.append(obligation.id)
            continue
        command = COMMANDS_BY_KIND.get(obligation.kind)
        if command is None:
            raise ValueError(f"Unsupported proof kind: {obligation.kind}")
        mappings.append(ProofMapping(obligation_id=obligation.id, test_command=command))
    return ProofPlan(
        eip_version=eip.version,
        eip_digest=compute_eip_digest(eip),
        mappings=mappings,
        required_manual_gates=manual_gates,
    )


def _ensure_proof_coverage(eip: EIP) -> None:
    invariant_count = sum(len(values) for values in eip.invariants.model_dump().values())
    success_count = len(eip.goal.success_criteria)
    if (invariant_count or success_count) and not eip.proof_obligations:
        raise ValueError("EIP has success criteria or invariants but no proof obligations.")
