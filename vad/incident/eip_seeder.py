"""EIP Seeder: Generates remediation Executable Intent Packages from post-mortem artifacts."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
from typing import Optional
import yaml

from vad.contracts.models import (
    AssuranceLevel,
    AutonomyTier,
    Constraints,
    EIP,
    Goal,
    Invariants,
    ModelBudget,
    ProofKind,
    ProofObligation,
    ReleaseRequirements,
    RiskTier,
    TelemetryRequirements,
    ToolPermissions,
    default_assurance_for_risk,
    default_freshness_for_risk,
)
from vad.incident.post_mortem import PostMortem
from vad.memory.contracts import (
    FactType,
    InferredLesson,
    LessonReviewStatus,
    ObservedFact,
    ProposedRuleType,
)
from vad.proof.plan import compute_eip_digest


def _resolve_risk_tier(severity: str, *, critical: bool = False) -> RiskTier:
    """Resolve remediation EIP risk tier from post-mortem severity and critical flag.

    Preserves RiskTier.HIGH for SEV-1/SEV-2/standard incidents and escalates to
    RiskTier.CRITICAL when critical=True or severity explicitly indicates critical/sev0.
    """
    if critical:
        return RiskTier.CRITICAL
    normalized = (severity or "").strip().lower()
    if (
        normalized in {"critical", "sev0", "sev-0"}
        or "critical" in normalized
        or "sev0" in normalized
        or "sev-0" in normalized
    ):
        return RiskTier.CRITICAL
    return RiskTier.HIGH


def _compute_post_mortem_digest(post_mortem: PostMortem) -> str:
    """Compute a deterministic 64-hex SHA-256 digest from canonical post-mortem payload."""
    payload = json.dumps(
        post_mortem.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


class EIPSeeder:
    """Extracts invariants and proof obligations from post-mortems to seed corrective EIPs."""

    def seed_from_post_mortem(
        self,
        post_mortem: PostMortem,
        output_dir: Optional[Path] = None,
        critical: bool = False,
    ) -> EIP:
        risk_tier = _resolve_risk_tier(post_mortem.severity, critical=critical)
        assurance_level = (
            AssuranceLevel.A4_RESTRICTED
            if risk_tier == RiskTier.CRITICAL
            else default_assurance_for_risk(risk_tier)
        )
        freshness_policy = default_freshness_for_risk(risk_tier)

        sec_invariants: list[str] = []
        func_invariants: list[str] = []
        operational_constraints: list[str] = []
        proof_obligations: list[ProofObligation] = []

        for idx, clause in enumerate(post_mortem.proposed_eip_clauses):
            if clause.kind == "security_invariant":
                sec_invariants.append(clause.description)
            elif clause.kind == "functional_invariant":
                func_invariants.append(clause.description)
            elif clause.kind == "constraint":
                operational_constraints.append(clause.description)
            elif clause.kind == "proof_obligation":
                proof_obligations.append(
                    ProofObligation(
                        id=f"po-remedy-{idx + 1}",
                        kind=(
                            ProofKind.SECURITY
                            if "security" in clause.description.lower()
                            else ProofKind.UNIT
                        ),
                        description=clause.description,
                        min_assurance_level=assurance_level,
                        independent_execution_required=True,
                    )
                )

        if not proof_obligations:
            proof_obligations.append(
                ProofObligation(
                    id="po-remedy-1",
                    kind=ProofKind.UNIT,
                    description=f"Regression tests validating fix for {post_mortem.incident_id}",
                    min_assurance_level=assurance_level,
                    independent_execution_required=True,
                )
            )

        eip = EIP(
            version="1.0.0",
            name=f"remediation-{post_mortem.incident_id.lower()}",
            goal=Goal(
                description=f"Remediate incident {post_mortem.incident_id}: {post_mortem.title}",
                success_criteria=[
                    "Implement architectural guardrails identified in post-mortem",
                    "All remediation proof obligations passing in throwaway container",
                ],
            ),
            non_goals=["Unrelated feature refactoring"],
            risk_tier=risk_tier,
            autonomy_tier=AutonomyTier.ASSISTED,
            scope_boundaries=["*"],
            invariants=Invariants(
                security=sec_invariants or ["Strict input sanitization enforced"],
                functional=func_invariants or ["Preserve backward compatibility"],
            ),
            constraints=Constraints(
                security=["Enforce remediation guardrails before release"],
                privacy=["No sensitive incident payload leakage in remediation artifacts"],
                operational=operational_constraints
                or ["Restrict remediation execution to approved verification tools"],
            ),
            proof_obligations=proof_obligations,
            tool_permissions=ToolPermissions(allowed=["pytest", "ruff"]),
            memory_requirements=[],
            model_budget=ModelBudget(max_tokens=4000, max_cost=2.0, max_loop_depth=3),
            release_requirements=ReleaseRequirements(
                required=True,
                strategy="gated",
                gates=["pytest", "dsse_review"],
            ),
            telemetry_requirements=TelemetryRequirements(
                required=True,
                signals=["audit_log", "traces", "metrics"],
            ),
            assurance_level=assurance_level,
            freshness_policy=freshness_policy,
        )

        effective_assurance = eip.effective_assurance_level()
        for obligation in eip.proof_obligations:
            obligation.independent_execution_required = True
            obligation.min_assurance_level = effective_assurance

        if output_dir:
            output_dir.mkdir(parents=True, exist_ok=True)
            target = output_dir / f"{eip.name}.yaml"
            target.write_text(
                yaml.safe_dump(eip.model_dump(mode="json"), sort_keys=False),
                encoding="utf-8",
            )

        return eip

    def extract_incident_fact_and_lesson(
        self,
        post_mortem: PostMortem,
        *,
        run_id: str,
        eip: EIP,
    ) -> tuple[ObservedFact, InferredLesson]:
        """Extract an authoritative incident fact and a candidate (unapproved) lesson."""
        source_evidence_digest = _compute_post_mortem_digest(post_mortem)
        eip_digest = compute_eip_digest(eip)
        short_digest = source_evidence_digest[:12]
        incident_slug = post_mortem.incident_id.lower()

        domain_tags = [
            "incident",
            "remediation",
            f"severity:{post_mortem.severity.strip().lower()}",
            f"risk:{eip.risk_tier.value}",
        ]

        fact = ObservedFact(
            fact_id=f"fact-incident-{incident_slug}-{short_digest}",
            fact_type=FactType.INCIDENT,
            run_id=run_id,
            eip_digest=eip_digest,
            source_evidence_digest=source_evidence_digest,
            risk_tier=eip.risk_tier.value,
            domain_tags=domain_tags,
            summary=(
                f"Incident {post_mortem.incident_id} ({post_mortem.severity}): "
                f"{post_mortem.title}. Root cause: {post_mortem.root_cause}"
            ),
            metadata={
                "post_mortem_id": post_mortem.id,
                "incident_id": post_mortem.incident_id,
                "severity": post_mortem.severity,
                "post_mortem_timestamp": post_mortem.timestamp,
                "eip_name": eip.name,
                "invariant_violations": list(post_mortem.invariant_violations),
                "contributing_factors": list(post_mortem.contributing_factors),
            },
        )

        obligation_descriptions = [po.description for po in eip.proof_obligations]
        recommendation = (
            f"Enforce independent remediation proof obligations for {post_mortem.incident_id}: "
            + "; ".join(obligation_descriptions)
            if obligation_descriptions
            else f"Add regression proof obligation addressing: {post_mortem.root_cause}"
        )

        lesson = InferredLesson(
            lesson_id=f"lesson-incident-{incident_slug}-{short_digest}",
            title=f"Remediation proof obligation for {post_mortem.incident_id}: {post_mortem.title}",
            recommendation=recommendation,
            proposed_rule_type=ProposedRuleType.PROOF_OBLIGATION,
            derived_from_fact_ids=[fact.fact_id],
            domain_tags=list(domain_tags),
            confidence=0.7,
            review_status=LessonReviewStatus.PROPOSED,
            reviewed_by=None,
            reviewed_at=None,
            promotion_record_id=None,
        )

        return fact, lesson


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description="Seed remediation EIP from post-mortem")
    parser.add_argument("--post-mortem", required=True, help="Path to post_mortem.yaml")
    parser.add_argument("--output-dir", default="eips", help="Output directory for seeded EIP")
    parser.add_argument(
        "--critical",
        action="store_true",
        help="Escalate seeded EIP to RiskTier.CRITICAL and A4_RESTRICTED assurance",
    )
    args = parser.parse_args(argv)

    pm_path = Path(args.post_mortem)
    if not pm_path.exists():
        print(f"Error: {pm_path} does not exist", file=sys.stderr)
        return 1

    data = yaml.safe_load(pm_path.read_text(encoding="utf-8"))
    pm = PostMortem(**data)
    seeder = EIPSeeder()
    eip = seeder.seed_from_post_mortem(
        pm,
        output_dir=Path(args.output_dir),
        critical=args.critical,
    )
    print(f"Successfully seeded EIP '{eip.name}' from post-mortem '{pm.id}'.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
