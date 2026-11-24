import re

import pytest
import yaml

from vad.contracts.models import (
    AssuranceLevel,
    Constraints,
    EIP,
    ReleaseRequirements,
    RiskTier,
    TelemetryRequirements,
    default_freshness_for_risk,
)
from vad.incident.eip_seeder import EIPSeeder
from vad.incident.post_mortem import (
    AlertThreshold,
    PostMortem,
    ProposedClause,
    TimelineEntry,
)
from vad.memory.authority_guard import MemoryAuthorityGuard
from vad.memory.contracts import (
    FactType,
    InferredLesson,
    LessonReviewStatus,
    ObservedFact,
    ProposedRuleType,
)
from vad.proof.plan import compute_eip_digest


def _make_post_mortem(
    *,
    severity: str = "SEV-1",
    clauses: list[ProposedClause] | None = None,
) -> PostMortem:
    return PostMortem(
        id="PM-V2-001",
        incident_id="INC-2026-001",
        title="Unbounded tool call bypassed verification gate",
        timestamp="2026-10-15T08:30:00Z",
        severity=severity,
        timeline=[
            TimelineEntry(
                timestamp="2026-10-15T08:29:00Z",
                event="Tool invocation skipped digest validation",
                actor="worker-agent",
            )
        ],
        contributing_factors=["Missing digest check on skill invocation"],
        invariant_violations=["Tool execution outside declared scope"],
        root_cause="Skill descriptor checksum was not verified prior to execution",
        proposed_eip_clauses=clauses
        if clauses is not None
        else [
            ProposedClause(
                kind="security_invariant",
                description="Verify SHA-256 digest before every tool invocation",
            ),
            ProposedClause(
                kind="functional_invariant",
                description="Preserve deterministic skill dispatch on valid checksums",
            ),
            ProposedClause(
                kind="constraint",
                description="Disallow dynamic skill loading outside pinned manifest",
            ),
            ProposedClause(
                kind="proof_obligation",
                description="Verify security checksum gate rejects tampered descriptors",
            ),
        ],
        alert_thresholds=[
            AlertThreshold(metric="checksum_mismatch", threshold="> 0", action="halt")
        ],
    )


def test_seed_from_post_mortem_aligns_with_eip_schema_and_high_defaults(tmp_path):
    pm = _make_post_mortem(severity="SEV-1")
    seeder = EIPSeeder()

    eip = seeder.seed_from_post_mortem(pm, output_dir=tmp_path)

    assert isinstance(eip, EIP)
    assert eip.risk_tier == RiskTier.HIGH
    assert eip.assurance_level == AssuranceLevel.A3_ATTESTED
    assert eip.effective_assurance_level() == AssuranceLevel.A3_ATTESTED
    assert eip.freshness_policy == default_freshness_for_risk(RiskTier.HIGH)

    assert isinstance(eip.constraints, Constraints)
    assert eip.constraints.security
    assert eip.constraints.privacy
    assert "Disallow dynamic skill loading outside pinned manifest" in eip.constraints.operational
    assert not eip.constraints.model_extra

    assert isinstance(eip.release_requirements, ReleaseRequirements)
    assert eip.release_requirements.required is True
    assert eip.release_requirements.strategy == "gated"
    assert eip.release_requirements.gates == ["pytest", "dsse_review"]
    assert not eip.release_requirements.model_extra

    assert isinstance(eip.telemetry_requirements, TelemetryRequirements)
    assert eip.telemetry_requirements.required is True
    assert eip.telemetry_requirements.signals == ["audit_log", "traces", "metrics"]
    assert not eip.telemetry_requirements.model_extra

    assert len(eip.proof_obligations) == 1
    obligation = eip.proof_obligations[0]
    assert obligation.independent_execution_required is True
    assert obligation.min_assurance_level == eip.effective_assurance_level()

    # Written YAML is cleanly parseable via yaml.safe_load and reconstructs EIP
    yaml_path = tmp_path / f"{eip.name}.yaml"
    assert yaml_path.exists()
    reloaded = EIP(**yaml.safe_load(yaml_path.read_text(encoding="utf-8")))
    assert reloaded == eip


def test_seed_from_post_mortem_fallback_obligation_sets_independent_and_assurance():
    pm = _make_post_mortem(
        severity="SEV-2",
        clauses=[
            ProposedClause(
                kind="security_invariant",
                description="Enforce strict boundary checks",
            )
        ],
    )
    eip = EIPSeeder().seed_from_post_mortem(pm)

    assert eip.risk_tier == RiskTier.HIGH
    assert len(eip.proof_obligations) == 1
    obligation = eip.proof_obligations[0]
    assert obligation.id == "po-remedy-1"
    assert obligation.independent_execution_required is True
    assert obligation.min_assurance_level == eip.effective_assurance_level()


@pytest.mark.parametrize(
    ("severity", "critical_flag"),
    [
        ("critical", False),
        ("CRITICAL", False),
        ("SEV-0", False),
        ("sev0", False),
        ("SEV-1", True),
    ],
)
def test_critical_severity_escalates_to_critical_and_a4_restricted(severity, critical_flag):
    pm = _make_post_mortem(severity=severity)
    eip = EIPSeeder().seed_from_post_mortem(pm, critical=critical_flag)

    assert eip.risk_tier == RiskTier.CRITICAL
    assert eip.assurance_level == AssuranceLevel.A4_RESTRICTED
    assert eip.effective_assurance_level() == AssuranceLevel.A4_RESTRICTED
    assert eip.freshness_policy == default_freshness_for_risk(RiskTier.CRITICAL)
    assert eip.freshness_policy.max_evidence_age_seconds == 900

    assert eip.proof_obligations
    for obligation in eip.proof_obligations:
        assert obligation.independent_execution_required is True
        assert obligation.min_assurance_level == AssuranceLevel.A4_RESTRICTED


def test_extract_incident_fact_and_lesson_emits_authoritative_fact_and_proposed_lesson():
    pm = _make_post_mortem(severity="critical")
    seeder = EIPSeeder()
    eip = seeder.seed_from_post_mortem(pm)

    fact, lesson = seeder.extract_incident_fact_and_lesson(
        pm,
        run_id="run-incident-42",
        eip=eip,
    )

    assert isinstance(fact, ObservedFact)
    assert fact.fact_type == FactType.INCIDENT
    assert fact.authoritative_source == "vad"
    assert fact.run_id == "run-incident-42"
    assert fact.eip_digest == compute_eip_digest(eip)
    assert fact.risk_tier == RiskTier.CRITICAL.value
    assert re.fullmatch(r"^[0-9a-f]{64}$", fact.source_evidence_digest)

    # Digest must be deterministic for the same canonical post-mortem payload
    fact_repeat, _ = seeder.extract_incident_fact_and_lesson(
        pm,
        run_id="run-incident-99",
        eip=eip,
    )
    assert fact.source_evidence_digest == fact_repeat.source_evidence_digest

    assert isinstance(lesson, InferredLesson)
    assert lesson.review_status == LessonReviewStatus.PROPOSED
    assert lesson.proposed_rule_type == ProposedRuleType.PROOF_OBLIGATION
    assert lesson.derived_from_fact_ids == [fact.fact_id]
    assert lesson.reviewed_by is None
    assert lesson.reviewed_at is None
    assert lesson.is_policy_eligible is False

    guard = MemoryAuthorityGuard()
    guard.validate_outbound_fact(fact)
    guard.validate_outbound_lesson(lesson)
