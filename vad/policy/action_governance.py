"""Action Governance Engine: Two-phase gate combining fast static policy rules with LLM semantic evaluation."""

from enum import Enum
from typing import Any, Dict, Optional
from pydantic import BaseModel
from vad.policy.autonomy_policy import (
    AutonomyPolicyEvaluator,
    AutonomyPolicyResource,
    PolicyActionDecision,
)
from vad.policy.llm_hooks import (
    ConfigurableLLMPolicyProvider,
    LLMPermissionDecision,
    LLMPolicyProvider,
)


class GovernanceDecision(str, Enum):
    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"


class ActionGovernanceResult(BaseModel):
    decision: GovernanceDecision
    reason: str
    phase: str  # "static_rule" or "llm_evaluator"
    risk_assessment: Optional[str] = None


class ActionGovernanceEngine:
    """Orchestrates two-phase action governance: static deterministic bounds followed by LLM parameter safety checks."""

    def __init__(
        self,
        policy_evaluator: Optional[AutonomyPolicyEvaluator] = None,
        llm_provider: Optional[LLMPolicyProvider] = None,
    ):
        self.policy_evaluator = policy_evaluator or AutonomyPolicyEvaluator(AutonomyPolicyResource())
        self.llm_provider = llm_provider or ConfigurableLLMPolicyProvider()

    def evaluate_action(
        self,
        tool_name: str,
        tool_input: Dict[str, Any],
        context: Optional[Dict[str, Any]] = None,
    ) -> ActionGovernanceResult:
        # Phase 1: Static Deterministic Policy Evaluation
        stat_dec, stat_reason = self.policy_evaluator.evaluate_tool(tool_name, context)

        if stat_dec == PolicyActionDecision.DENY:
            return ActionGovernanceResult(
                decision=GovernanceDecision.DENY,
                reason=f"[Phase 1 Static Block] {stat_reason}",
                phase="static_rule",
                risk_assessment="Action explicitly prohibited by AutonomyPolicy",
            )
        elif stat_dec == PolicyActionDecision.ESCALATE:
            return ActionGovernanceResult(
                decision=GovernanceDecision.ASK,
                reason=f"[Phase 1 Static Escalate] {stat_reason}",
                phase="static_rule",
                risk_assessment="Requires human confirmation per policy trigger",
            )

        # Phase 2: LLM Evaluation for parameterized actions
        policy_ctx = f"Policy: maturity_level={self.policy_evaluator.policy.maturity_level}"
        llm_eval = self.llm_provider.evaluate(tool_name, tool_input, policy_ctx)

        gov_dec = (
            GovernanceDecision.ALLOW
            if llm_eval.decision == LLMPermissionDecision.ALLOW
            else (
                GovernanceDecision.DENY
                if llm_eval.decision == LLMPermissionDecision.DENY
                else GovernanceDecision.ASK
            )
        )

        return ActionGovernanceResult(
            decision=gov_dec,
            reason=f"[Phase 2 LLM] {llm_eval.reason}",
            phase="llm_evaluator",
            risk_assessment=llm_eval.risk_assessment,
        )
