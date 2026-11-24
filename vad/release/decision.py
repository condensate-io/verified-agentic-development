"""Replayable Release Decision Engine driven by the Evidence-Driven Autonomy Matrix."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List, Optional
from uuid import uuid4
from enum import Enum
from pydantic import BaseModel, Field

from vad.contracts.models import AssuranceLevel, RiskTier
from vad.policy.autonomy_matrix import (
    AutonomyEvaluationInput,
    AutonomyEvaluationResult,
    AutonomyMatrixEvaluator,
    PolicyStage,
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ReleaseOutcome(str, Enum):
    """Canonical release decision outcomes across autonomy and fail-closed gates."""

    AUTONOMOUS_RELEASE = "autonomous_release"
    APPROVED_WITH_HUMAN = "approved_with_human"
    APPROVED_RESTRICTED = "approved_restricted"
    HUMAN_APPROVAL_REQUIRED = "human_approval_required"
    RESTRICTED_AUTHORIZATION_REQUIRED = "restricted_authorization_required"
    BLOCKED_STALE_EVIDENCE = "blocked_stale_evidence"
    BLOCKED_INSUFFICIENT_ASSURANCE = "blocked_insufficient_assurance"
    BLOCKED_DRIFT = "blocked_drift"
    DENIED_POLICY = "denied_policy"


_BLOCK_CODE_TO_OUTCOME: Dict[str, ReleaseOutcome] = {
    "blocked_drift": ReleaseOutcome.BLOCKED_DRIFT,
    "blocked_stale_evidence": ReleaseOutcome.BLOCKED_STALE_EVIDENCE,
    "blocked_insufficient_assurance": ReleaseOutcome.BLOCKED_INSUFFICIENT_ASSURANCE,
    "human_approval_required": ReleaseOutcome.HUMAN_APPROVAL_REQUIRED,
    "restricted_authorization_required": ReleaseOutcome.RESTRICTED_AUTHORIZATION_REQUIRED,
    "denied_policy": ReleaseOutcome.DENIED_POLICY,
}

_APPROVED_TIER_TO_OUTCOME: Dict[RiskTier, ReleaseOutcome] = {
    RiskTier.LOW: ReleaseOutcome.AUTONOMOUS_RELEASE,
    RiskTier.MEDIUM: ReleaseOutcome.AUTONOMOUS_RELEASE,
    RiskTier.HIGH: ReleaseOutcome.APPROVED_WITH_HUMAN,
    RiskTier.CRITICAL: ReleaseOutcome.APPROVED_RESTRICTED,
}


class ReleaseDecision(BaseModel):
    """Immutable, replayable release decision record bound to EIP and evidence digests."""

    decision_id: str = Field(default_factory=lambda: f"dec-{uuid4().hex[:12]}")
    run_id: str
    eip_digest: str
    evidence_digest: str
    risk_tier: RiskTier
    stage: PolicyStage
    assurance_required: AssuranceLevel
    assurance_achieved: AssuranceLevel
    outcome: ReleaseOutcome
    can_release: bool
    reasons: List[str] = Field(default_factory=list)
    required_signoffs: int = Field(ge=0)
    recorded_approvers: List[str] = Field(default_factory=list)
    explicit_authorization_ref: Optional[str] = None
    decided_at: datetime = Field(default_factory=_utc_now)


class ReleaseDecisionEngine:
    """Produces replayable ReleaseDecision records from AutonomyMatrixEvaluator outcomes."""

    def __init__(self, evaluator: Optional[AutonomyMatrixEvaluator] = None):
        self.evaluator = evaluator or AutonomyMatrixEvaluator()

    @staticmethod
    def _resolve_outcome(eval_input: AutonomyEvaluationInput, eval_result: AutonomyEvaluationResult) -> ReleaseOutcome:
        if eval_result.allowed:
            return _APPROVED_TIER_TO_OUTCOME[eval_input.risk_tier]
        return _BLOCK_CODE_TO_OUTCOME.get(eval_result.block_reason_code or "", ReleaseOutcome.DENIED_POLICY)

    def decide(
        self,
        run_id: str,
        eip_digest: str,
        evidence_digest: str,
        eval_input: AutonomyEvaluationInput,
    ) -> ReleaseDecision:
        """Evaluate autonomy rules and emit a replayable ReleaseDecision."""
        eval_result = self.evaluator.evaluate(eval_input)
        outcome = self._resolve_outcome(eval_input, eval_result)
        auth_ref = eval_input.explicit_authorization_ref.strip() if eval_input.explicit_authorization_ref else None
        return ReleaseDecision(
            run_id=run_id,
            eip_digest=eip_digest,
            evidence_digest=evidence_digest,
            risk_tier=eval_input.risk_tier,
            stage=eval_input.stage,
            assurance_required=eval_result.required_assurance,
            assurance_achieved=eval_result.assurance_achieved,
            outcome=outcome,
            can_release=eval_result.allowed,
            reasons=eval_result.reasons,
            required_signoffs=eval_result.required_signoffs,
            recorded_approvers=eval_result.distinct_human_approvers,
            explicit_authorization_ref=auth_ref or None,
        )
