"""EIP Seeder: Generates remediation Executable Intent Packages from post-mortem artifacts."""

import argparse
import json
from pathlib import Path
import sys
from typing import Optional
import yaml

from vad.contracts.models import (
    EIP,
    Goal,
    ProofObligation,
    ProofKind,
    Invariants,
    Constraints,
    ToolPermissions,
    ModelBudget,
    ReleaseRequirements,
    TelemetryRequirements,
    RiskTier,
    AutonomyTier,
)
from vad.incident.post_mortem import PostMortem


class EIPSeeder:
    """Extracts invariants and proof obligations from post-mortems to seed corrective EIPs."""

    def seed_from_post_mortem(
        self,
        post_mortem: PostMortem,
        output_dir: Optional[Path] = None,
    ) -> EIP:
        sec_invariants = []
        func_invariants = []
        proof_obligations = []

        for idx, clause in enumerate(post_mortem.proposed_eip_clauses):
            if clause.kind == "security_invariant":
                sec_invariants.append(clause.description)
            elif clause.kind == "functional_invariant":
                func_invariants.append(clause.description)
            elif clause.kind == "proof_obligation":
                proof_obligations.append(
                    ProofObligation(
                        id=f"po-remedy-{idx+1}",
                        kind=ProofKind.SECURITY if "security" in clause.description.lower() else ProofKind.UNIT,
                        description=clause.description,
                    )
                )

        if not proof_obligations:
            proof_obligations.append(
                ProofObligation(
                    id="po-remedy-1",
                    kind=ProofKind.UNIT,
                    description=f"Regression tests validating fix for {post_mortem.incident_id}",
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
            risk_tier=RiskTier.HIGH,
            autonomy_tier=AutonomyTier.ASSISTED,
            scope_boundaries=["*"],
            invariants=Invariants(
                security=sec_invariants or ["Strict input sanitization enforced"],
                functional=func_invariants or ["Preserve backward compatibility"],
            ),
            constraints=Constraints(
                tool_permissions=ToolPermissions(allowed=["pytest", "ruff"]),
                model_budget=ModelBudget(max_tokens=4000, max_cost=2.0, max_loop_depth=3),
            ),
            proof_obligations=proof_obligations,
            tool_permissions=ToolPermissions(allowed=["pytest"]),
            memory_requirements=[],
            model_budget=ModelBudget(max_tokens=4000, max_cost=2.0, max_loop_depth=3),
            release_requirements=ReleaseRequirements(required=True, required_checks=["pytest", "dsse_review"]),
            telemetry_requirements=TelemetryRequirements(required=True, audit_level="verbose"),
        )

        if output_dir:
            output_dir.mkdir(parents=True, exist_ok=True)
            target = output_dir / f"{eip.name}.yaml"
            target.write_text(yaml.dump(eip.model_dump(), sort_keys=False), encoding="utf-8")

        return eip


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description="Seed remediation EIP from post-mortem")
    parser.add_argument("--post-mortem", required=True, help="Path to post_mortem.yaml")
    parser.add_argument("--output-dir", default="eips", help="Output directory for seeded EIP")
    args = parser.parse_args(argv)

    pm_path = Path(args.post_mortem)
    if not pm_path.exists():
        print(f"Error: {pm_path} does not exist", file=sys.stderr)
        return 1

    data = yaml.safe_load(pm_path.read_text(encoding="utf-8"))
    pm = PostMortem(**data)
    seeder = EIPSeeder()
    eip = seeder.seed_from_post_mortem(pm, output_dir=Path(args.output_dir))
    print(f"Successfully seeded EIP '{eip.name}' from post-mortem '{pm.id}'.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
