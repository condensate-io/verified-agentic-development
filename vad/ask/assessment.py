from enum import Enum
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
)


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

    @property
    def blocks_autonomous_execution(self) -> bool:
        return bool(self.clarification_questions) or self.autonomy_tier == AutonomyTier.MANUAL


def eip_from_assessment(name: str, assessment: AskAssessment) -> EIP:
    proof_obligations = [
        ProofObligation(
            id=f"po-{index}",
            kind=kind,
            description=f"Provide {kind} proof for: {assessment.summary}",
        )
        for index, kind in enumerate(assessment.required_proof_kinds, start=1)
    ]
    if assessment.autonomy_tier == AutonomyTier.MANUAL:
        proof_obligations.append(
            ProofObligation(
                id=f"po-{len(proof_obligations) + 1}",
                kind="manual_gate",
                description="Human approval required before autonomous implementation.",
            )
        )

    return EIP(
        version="1.0.0",
        name=name,
        goal=Goal(description=assessment.summary, success_criteria=["All proof obligations pass."]),
        non_goals=[],
        risk_tier=assessment.risk_tier,
        autonomy_tier=assessment.autonomy_tier,
        scope_boundaries=[],
        invariants=Invariants(functional=["Implementation satisfies the assessed ask."]),
        constraints=Constraints(operational=assessment.clarification_questions),
        proof_obligations=proof_obligations,
        tool_permissions=ToolPermissions(allowed=assessment.tool_needs, denied=["network"]),
        memory_requirements=[
            MemoryRequirement(scope=scope, purpose="ask assessment context")
            for scope in assessment.memory_needs
        ],
        model_budget=ModelBudget(
            max_tokens=assessment.token_budget,
            max_cost=assessment.budget,
            max_loop_depth=assessment.loop_depth,
        ),
        release_requirements=ReleaseRequirements(
            required=assessment.release_needed,
            strategy="gated" if assessment.release_needed else None,
            gates=["health"] if assessment.release_needed else [],
        ),
        telemetry_requirements=TelemetryRequirements(
            required=assessment.telemetry_needed,
            signals=["traces", "metrics"] if assessment.telemetry_needed else [],
        ),
    )
