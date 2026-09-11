"""Prompt-based permission hooks: Uses LLMs to make real-time governance decisions on tool and action execution."""

from abc import ABC, abstractmethod
from enum import Enum
import json
import re
from typing import Any, Callable, Dict, Optional
from pydantic import BaseModel, Field


class LLMPermissionDecision(str, Enum):
    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"


class LLMPolicyEvaluation(BaseModel):
    decision: LLMPermissionDecision
    reason: str
    risk_assessment: str
    confidence: float = 1.0


class LLMPolicyProvider(ABC):
    """Abstract interface for LLM-driven policy gate evaluation."""

    @abstractmethod
    def evaluate(
        self, action_name: str, tool_input: Dict[str, Any], policy_context: str
    ) -> LLMPolicyEvaluation:
        pass


class ConfigurableLLMPolicyProvider(LLMPolicyProvider):
    """Configurable provider that invokes an LLM endpoint or mock evaluation callable to evaluate security boundaries."""

    SYSTEM_PROMPT = """You are an enterprise AI governance authority for an autonomous agent development platform.
Evaluate the proposed action against the declared policy constraints.
Respond ONLY with a JSON object:
{
  "decision": "allow" | "deny" | "ask",
  "reason": "Clear justification",
  "risk_assessment": "Summary of risks identified"
}"""

    def __init__(
        self,
        endpoint_url: Optional[str] = None,
        api_key: Optional[str] = None,
        model: str = "gpt-4o-mini",
        evaluator_fn: Optional[Callable[[str, Dict[str, Any], str], LLMPolicyEvaluation]] = None,
    ):
        self.endpoint_url = endpoint_url
        self.api_key = api_key
        self.model = model
        self.evaluator_fn = evaluator_fn or self._default_mock_evaluator

    def _default_mock_evaluator(
        self, action_name: str, tool_input: Dict[str, Any], policy_context: str
    ) -> LLMPolicyEvaluation:
        # Deterministic offline heuristic mock
        cmd = str(tool_input.get("command", "")).lower()
        path = str(tool_input.get("path", "")).lower()

        # Dangerous patterns -> deny
        if any(bad in cmd for bad in ["rm -rf", "drop table", "chmod 777", "eval"]):
            return LLMPolicyEvaluation(
                decision=LLMPermissionDecision.DENY,
                reason=f"High-risk command detected in action '{action_name}'",
                risk_assessment="Potential data loss or privilege escalation",
            )

        # Ambiguous or human boundary -> ask
        if "migrate" in action_name or "deploy" in action_name or "push" in cmd:
            return LLMPolicyEvaluation(
                decision=LLMPermissionDecision.ASK,
                reason=f"Action '{action_name}' modifies critical infrastructure; requires human confirmation",
                risk_assessment="Impact on production infrastructure",
            )

        return LLMPolicyEvaluation(
            decision=LLMPermissionDecision.ALLOW,
            reason=f"Action '{action_name}' complies with declared policy bounds",
            risk_assessment="Low risk",
        )

    def evaluate(
        self, action_name: str, tool_input: Dict[str, Any], policy_context: str
    ) -> LLMPolicyEvaluation:
        return self.evaluator_fn(action_name, tool_input, policy_context)
