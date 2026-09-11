"""AutonomyPolicy: Declarative YAML Kubernetes-style resource for AI agent governance and bounds."""

from enum import Enum
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Tuple
import yaml
from pydantic import BaseModel, Field


class PolicyActionDecision(str, Enum):
    ALLOW = "allow"
    DENY = "deny"
    ESCALATE = "escalate"


class PolicyScope(BaseModel):
    target_type: str = "global"  # "global", "team", "repo"
    target_name: str = "*"


class HumanInTheLoopTrigger(BaseModel):
    action_pattern: str
    reason: str
    condition: Optional[str] = None


class AutonomyPolicyResource(BaseModel):
    apiVersion: str = "governance.vad.dev/v1"
    kind: str = "AutonomyPolicy"
    metadata: Dict[str, Any] = Field(default_factory=lambda: {"name": "default-policy"})
    maturity_level: int = 2
    scope: PolicyScope = Field(default_factory=PolicyScope)
    allowed_tools: List[str] = Field(default_factory=lambda: ["*"])
    blocked_tools: List[str] = Field(default_factory=list)
    max_cost_ceiling_usd: float = 10.0
    max_token_ceiling: int = 100000
    hitl_triggers: List[HumanInTheLoopTrigger] = Field(default_factory=list)

    @classmethod
    def from_yaml(cls, yaml_content: str) -> "AutonomyPolicyResource":
        data = yaml.safe_load(yaml_content) or {}
        return cls(**data)

    @classmethod
    def from_file(cls, path: str) -> "AutonomyPolicyResource":
        p = Path(path)
        with open(p, "r", encoding="utf-8") as f:
            return cls.from_yaml(f.read())


class AutonomyPolicyEvaluator:
    """Evaluates runtime actions and resource ceilings against an AutonomyPolicy."""

    def __init__(self, policy: AutonomyPolicyResource):
        self.policy = policy

    def evaluate_tool(self, tool_name: str, context: Optional[Dict[str, Any]] = None) -> Tuple[PolicyActionDecision, str]:
        # 1. Check blocked tools
        for blocked in self.policy.blocked_tools:
            if blocked == "*" or blocked.lower() == tool_name.lower():
                return PolicyActionDecision.DENY, f"Tool '{tool_name}' is explicitly blocked by AutonomyPolicy"

        # 2. Check HITL triggers
        for trigger in self.policy.hitl_triggers:
            if re.search(trigger.action_pattern, tool_name, re.IGNORECASE):
                return PolicyActionDecision.ESCALATE, f"Human escalation required: {trigger.reason}"

        # 3. Check allowed tools
        allowed = self.policy.allowed_tools
        if "*" in allowed or any(a.lower() == tool_name.lower() for a in allowed):
            return PolicyActionDecision.ALLOW, f"Tool '{tool_name}' allowed by policy"

        return PolicyActionDecision.DENY, f"Tool '{tool_name}' not in allowed list"

    def evaluate_budget(self, tokens_used: int, cost_usd: float) -> Tuple[PolicyActionDecision, str]:
        if tokens_used > self.policy.max_token_ceiling:
            return PolicyActionDecision.DENY, f"Token limit exceeded ({tokens_used} > {self.policy.max_token_ceiling})"
        if cost_usd > self.policy.max_cost_ceiling_usd:
            return PolicyActionDecision.DENY, f"Cost ceiling exceeded (${cost_usd:.4f} > ${self.policy.max_cost_ceiling_usd:.4f})"
        return PolicyActionDecision.ALLOW, "Budget within bounds"
