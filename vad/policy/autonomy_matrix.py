"""Evidence-driven Autonomy Matrix across RiskTier x Assurance x Freshness x Stage."""
from __future__ import annotations

from enum import Enum
from pathlib import Path
import shutil
from typing import Dict, List, Optional
from pydantic import BaseModel, Field

from vad.contracts.models import AssuranceLevel, RiskTier, _ASSURANCE_RANK
from vad.policy.engine import PolicyEngine


class PolicyStage(str, Enum):
    """Lifecycle stages where autonomy matrix policy is enforced."""

    BUILD = "build"
    CI_CD = "ci_cd"
    DEPLOY = "deploy"
    RUNTIME = "runtime"
    DRIFT = "drift"


class AllowedAutonomyAction(str, Enum):
    """Permitted autonomy gate action for a risk tier evaluation."""

    AUTONOMOUS_MERGE = "autonomous_merge"
    INDEPENDENT_VERIFICATION_REQUIRED = "independent_verification_required"
    HUMAN_APPROVAL_REQUIRED = "human_approval_required"
    RESTRICTED_EXECUTION_AUTHORIZATION = "restricted_execution_authorization"
    DENY = "deny"


class AutonomyMatrixRule(BaseModel):
    """Policy rule defining assurance, verifier, and approval requirements for a tier."""

    required_assurance: AssuranceLevel
    require_independent_verifier: bool
    require_human_approval: bool
    require_explicit_authorization: bool
    required_distinct_signoffs: int = Field(ge=0)
    allowed_action: AllowedAutonomyAction


DEFAULT_AUTONOMY_MATRIX: Dict[RiskTier, AutonomyMatrixRule] = {
    RiskTier.LOW: AutonomyMatrixRule(
        required_assurance=AssuranceLevel.A1_BASIC,
        require_independent_verifier=False,
        require_human_approval=False,
        require_explicit_authorization=False,
        required_distinct_signoffs=0,
        allowed_action=AllowedAutonomyAction.AUTONOMOUS_MERGE,
    ),
    RiskTier.MEDIUM: AutonomyMatrixRule(
        required_assurance=AssuranceLevel.A2_INDEPENDENT,
        require_independent_verifier=True,
        require_human_approval=False,
        require_explicit_authorization=False,
        required_distinct_signoffs=1,
        allowed_action=AllowedAutonomyAction.INDEPENDENT_VERIFICATION_REQUIRED,
    ),
    RiskTier.HIGH: AutonomyMatrixRule(
        required_assurance=AssuranceLevel.A3_ATTESTED,
        require_independent_verifier=True,
        require_human_approval=True,
        require_explicit_authorization=False,
        required_distinct_signoffs=2,
        allowed_action=AllowedAutonomyAction.HUMAN_APPROVAL_REQUIRED,
    ),
    RiskTier.CRITICAL: AutonomyMatrixRule(
        required_assurance=AssuranceLevel.A4_RESTRICTED,
        require_independent_verifier=True,
        require_human_approval=True,
        require_explicit_authorization=True,
        required_distinct_signoffs=2,
        allowed_action=AllowedAutonomyAction.RESTRICTED_EXECUTION_AUTHORIZATION,
    ),
}


class AutonomyEvaluationInput(BaseModel):
    """Normalized inputs for evaluating evidence-driven release autonomy."""

    risk_tier: RiskTier
    stage: PolicyStage = PolicyStage.CI_CD
    verification_passed: bool
    assurance_achieved: AssuranceLevel
    independent_verifier: bool
    evidence_fresh: bool
    freshness_reasons: List[str] = Field(default_factory=list)
    drift_detected: bool = False
    human_approvers: List[str] = Field(default_factory=list)
    builder_actor: Optional[str] = None
    verifier_actor: Optional[str] = None
    explicit_authorization_ref: Optional[str] = None


class AutonomyEvaluationResult(BaseModel):
    """Deterministic outcome from evaluating AutonomyEvaluationInput."""

    allowed: bool
    action: AllowedAutonomyAction
    block_reason_code: Optional[str] = None
    reasons: List[str] = Field(default_factory=list)
    rule: AutonomyMatrixRule
    required_assurance: AssuranceLevel
    assurance_achieved: AssuranceLevel
    distinct_human_approvers: List[str] = Field(default_factory=list)
    required_signoffs: int = 0
    opa_checked: bool = False
    opa_consistent: Optional[bool] = None


def _normalize_actor(actor: Optional[str]) -> Optional[str]:
    if actor is None:
        return None
    stripped = actor.strip()
    return stripped if stripped else None


def _dedupe_actors(actors: List[str]) -> List[str]:
    seen: set[str] = set()
    ordered: List[str] = []
    for raw in actors:
        normalized = _normalize_actor(raw)
        if normalized and normalized not in seen:
            seen.add(normalized)
            ordered.append(normalized)
    return ordered


class AutonomyMatrixEvaluator:
    """Evaluates autonomy matrix rules deterministically with optional OPA cross-check."""

    def __init__(
        self,
        matrix: Optional[Dict[RiskTier, AutonomyMatrixRule]] = None,
        policy_path: str = "policies/vad.rego",
        use_opa: bool = False,
        policy_engine: Optional[PolicyEngine] = None,
    ):
        self.matrix = matrix or DEFAULT_AUTONOMY_MATRIX
        self.policy_path = policy_path
        self.use_opa = use_opa
        self._policy_engine = policy_engine or PolicyEngine()

    def _resolve_policy_path(self) -> str:
        candidate = Path(self.policy_path)
        if candidate.exists():
            return str(candidate)
        repo_relative = Path(__file__).resolve().parents[2] / self.policy_path
        if repo_relative.exists():
            return str(repo_relative)
        return self.policy_path

    def _build_opa_input(self, eval_input: AutonomyEvaluationInput, distinct_approvers: List[str]) -> dict:
        return {
            "action": "evaluate_release_autonomy",
            "risk_tier": eval_input.risk_tier.value,
            "stage": eval_input.stage.value,
            "verification_passed": eval_input.verification_passed,
            "assurance_achieved": eval_input.assurance_achieved.value,
            "independent_verifier": eval_input.independent_verifier,
            "evidence_fresh": eval_input.evidence_fresh,
            "drift_detected": eval_input.drift_detected,
            "human_approvers": distinct_approvers,
            "builder_actor": _normalize_actor(eval_input.builder_actor),
            "verifier_actor": _normalize_actor(eval_input.verifier_actor),
            "explicit_authorization_ref": _normalize_actor(eval_input.explicit_authorization_ref),
        }

    def _cross_check_opa(
        self,
        eval_input: AutonomyEvaluationInput,
        distinct_approvers: List[str],
        python_result: AutonomyEvaluationResult,
    ) -> AutonomyEvaluationResult:
        if not self.use_opa or shutil.which("opa") is None:
            return python_result

        resolved_path = self._resolve_policy_path()
        opa_input = self._build_opa_input(eval_input, distinct_approvers)
        deny_stale = self._policy_engine.evaluate(resolved_path, "data.vad.policy.deny_stale_evidence", opa_input) is True
        deny_drift = self._policy_engine.evaluate(resolved_path, "data.vad.policy.deny_drift_detected", opa_input) is True
        deny_assurance = (
            self._policy_engine.evaluate(resolved_path, "data.vad.policy.deny_insufficient_assurance", opa_input) is True
        )
        allow_auto = (
            self._policy_engine.evaluate(resolved_path, "data.vad.policy.allow_autonomous_release", opa_input) is True
        )
        req_human = (
            self._policy_engine.evaluate(resolved_path, "data.vad.policy.require_human_approval", opa_input) is True
        )
        req_restricted = (
            self._policy_engine.evaluate(resolved_path, "data.vad.policy.require_restricted_authorization", opa_input)
            is True
        )

        opa_denied = deny_stale or deny_drift or deny_assurance
        consistent = True
        if python_result.allowed and opa_denied:
            consistent = False
        elif eval_input.risk_tier in (RiskTier.LOW, RiskTier.MEDIUM) and python_result.allowed != allow_auto:
            consistent = False
        elif eval_input.risk_tier == RiskTier.HIGH and python_result.block_reason_code is None and not req_human:
            consistent = False
        elif eval_input.risk_tier == RiskTier.CRITICAL and python_result.block_reason_code is None and not req_restricted:
            consistent = False

        updates: dict = {"opa_checked": True, "opa_consistent": consistent}
        if not consistent and python_result.allowed:
            # Fail-closed if OPA disagrees with an allow decision.
            updates["allowed"] = False
            updates["action"] = AllowedAutonomyAction.DENY
            updates["block_reason_code"] = "denied_policy"
            updates["reasons"] = [*python_result.reasons, "OPA Rego cross-check denied release autonomy"]
        return python_result.model_copy(update=updates)

    def _deny(
        self,
        rule: AutonomyMatrixRule,
        eval_input: AutonomyEvaluationInput,
        distinct_approvers: List[str],
        block_reason_code: str,
        reasons: List[str],
        action: AllowedAutonomyAction = AllowedAutonomyAction.DENY,
    ) -> AutonomyEvaluationResult:
        return AutonomyEvaluationResult(
            allowed=False,
            action=action,
            block_reason_code=block_reason_code,
            reasons=reasons,
            rule=rule,
            required_assurance=rule.required_assurance,
            assurance_achieved=eval_input.assurance_achieved,
            distinct_human_approvers=distinct_approvers,
            required_signoffs=rule.required_distinct_signoffs,
        )

    def evaluate(self, eval_input: AutonomyEvaluationInput) -> AutonomyEvaluationResult:
        """Evaluate evidence, assurance, stage, and separation-of-duties rules."""
        rule = self.matrix[eval_input.risk_tier]
        builder = _normalize_actor(eval_input.builder_actor)
        verifier = _normalize_actor(eval_input.verifier_actor)
        distinct_approvers = _dedupe_actors(eval_input.human_approvers)
        auth_ref = _normalize_actor(eval_input.explicit_authorization_ref)

        result: Optional[AutonomyEvaluationResult] = None

        if eval_input.drift_detected:
            result = self._deny(
                rule,
                eval_input,
                distinct_approvers,
                "blocked_drift",
                [f"Drift detected at stage '{eval_input.stage.value}'; autonomous release blocked"],
            )
        elif not eval_input.evidence_fresh:
            reasons = list(eval_input.freshness_reasons) or ["Verification evidence is stale or expired"]
            result = self._deny(
                rule,
                eval_input,
                distinct_approvers,
                "blocked_stale_evidence",
                reasons,
            )
        elif not eval_input.verification_passed:
            result = self._deny(
                rule,
                eval_input,
                distinct_approvers,
                "denied_policy",
                ["Verification failed or not completed; fail-closed deny"],
            )
        elif _ASSURANCE_RANK[eval_input.assurance_achieved] < _ASSURANCE_RANK[rule.required_assurance]:
            result = self._deny(
                rule,
                eval_input,
                distinct_approvers,
                "blocked_insufficient_assurance",
                [
                    f"Assurance '{eval_input.assurance_achieved.value}' is below required "
                    f"'{rule.required_assurance.value}' for risk tier '{eval_input.risk_tier.value}'"
                ],
            )
        elif builder and builder in distinct_approvers:
            result = self._deny(
                rule,
                eval_input,
                distinct_approvers,
                "denied_policy",
                [f"Separation-of-duties violation: builder '{builder}' cannot self-approve"],
            )
        elif builder and verifier and rule.require_independent_verifier and builder == verifier:
            result = self._deny(
                rule,
                eval_input,
                distinct_approvers,
                "denied_policy",
                [f"Separation-of-duties violation: builder '{builder}' cannot act as independent verifier"],
            )
        elif rule.require_independent_verifier and not eval_input.independent_verifier:
            result = self._deny(
                rule,
                eval_input,
                distinct_approvers,
                "denied_policy",
                [f"Independent verification is required for risk tier '{eval_input.risk_tier.value}'"],
                action=AllowedAutonomyAction.INDEPENDENT_VERIFICATION_REQUIRED,
            )
        elif eval_input.risk_tier == RiskTier.HIGH and len(distinct_approvers) < 1:
            result = self._deny(
                rule,
                eval_input,
                distinct_approvers,
                "human_approval_required",
                ["High risk tier requires at least 1 distinct human release guardian approval"],
                action=AllowedAutonomyAction.HUMAN_APPROVAL_REQUIRED,
            )
        elif eval_input.risk_tier == RiskTier.CRITICAL and (not auth_ref or len(distinct_approvers) < 2):
            missing: List[str] = []
            if not auth_ref:
                missing.append("explicit pre-run operator authorization reference")
            if len(distinct_approvers) < 2:
                missing.append(
                    f"2 distinct human release guardian approvals (found {len(distinct_approvers)})"
                )
            result = self._deny(
                rule,
                eval_input,
                distinct_approvers,
                "restricted_authorization_required",
                [f"Critical risk tier requires {' and '.join(missing)}"],
                action=AllowedAutonomyAction.RESTRICTED_EXECUTION_AUTHORIZATION,
            )
        else:
            result = AutonomyEvaluationResult(
                allowed=True,
                action=rule.allowed_action,
                block_reason_code=None,
                reasons=[
                    f"Autonomy policy satisfied for risk tier '{eval_input.risk_tier.value}' "
                    f"at stage '{eval_input.stage.value}'"
                ],
                rule=rule,
                required_assurance=rule.required_assurance,
                assurance_achieved=eval_input.assurance_achieved,
                distinct_human_approvers=distinct_approvers,
                required_signoffs=rule.required_distinct_signoffs,
            )

        return self._cross_check_opa(eval_input, distinct_approvers, result)
