"""Definition of Ready (DoR) Gate evaluator for Executable Intent Packages (EIP)."""

from enum import Enum
from typing import List, Optional
from pydantic import BaseModel, Field
from vad.contracts.models import EIP, RiskTier


class DoRStatus(str, Enum):
    ADMIT = "admit"
    REJECT = "reject"
    DEFER = "defer"


class DoREvaluationResult(BaseModel):
    status: DoRStatus
    admitted: bool
    reason: str
    missing_criteria: List[str] = Field(default_factory=list)
    recommendations: List[str] = Field(default_factory=list)


class DoRGate:
    """Evaluates whether an incoming EIP meets Definition of Ready gates before task admission."""

    def __init__(self, strict_mode: bool = True):
        self.strict_mode = strict_mode

    def evaluate(self, eip: EIP) -> DoREvaluationResult:
        missing = []
        recommendations = []

        # 1. Goal completeness
        if not eip.goal.description or len(eip.goal.description.strip()) < 5:
            missing.append("Goal description must be at least 5 characters")
        if not eip.goal.success_criteria:
            missing.append("At least one success criterion must be defined")

        # 2. Scope boundaries
        if not eip.scope_boundaries:
            missing.append("Scope boundaries cannot be empty")

        # 3. Proof obligations
        if not eip.proof_obligations:
            missing.append("At least one proof obligation must be specified")

        # 4. Model budget bounds
        if eip.model_budget.max_tokens <= 0:
            missing.append("Model budget max_tokens must be greater than 0")

        # 5. Risk-tier invariants
        if eip.risk_tier == RiskTier.HIGH:
            if not eip.invariants.security:
                missing.append("High-risk EIP requires at least one security invariant")
            if not eip.constraints.security:
                recommendations.append("High-risk EIP should specify security constraints")

        # 6. Evaluation decision
        if missing:
            # If only minor criteria missing or non_goals missing, defer; otherwise reject
            if len(missing) == 1 and not eip.non_goals:
                return DoREvaluationResult(
                    status=DoRStatus.DEFER,
                    admitted=False,
                    reason=f"DoR deferred: {missing[0]}",
                    missing_criteria=missing,
                    recommendations=recommendations,
                )
            return DoREvaluationResult(
                status=DoRStatus.REJECT,
                admitted=False,
                reason=f"DoR rejected: {len(missing)} requirement(s) failed: {', '.join(missing)}",
                missing_criteria=missing,
                recommendations=recommendations,
            )

        return DoREvaluationResult(
            status=DoRStatus.ADMIT,
            admitted=True,
            reason="DoR gate passed: All Definition of Ready criteria satisfied",
            missing_criteria=[],
            recommendations=recommendations,
        )
