"""Operator Router: Maps hybrid routing decisions to ordered agent runner priority lists under policy constraints."""

from typing import Dict, List, Optional
from pydantic import BaseModel, Field
from vad.decision.hybrid_router import CostTier, HybridRouter, HybridRoutingDecision
from vad.decision.lexical_router import SkillDescriptor


class OperatorRoutingPolicy(BaseModel):
    domain_runners: Dict[str, List[str]] = Field(
        default_factory=lambda: {
            "security": ["claude", "aider"],
            "signaling": ["cursor", "claude"],
            "infrastructure": ["copilot", "claude"],
            "general": ["claude", "cursor", "codex", "aider"],
        }
    )
    disallowed_runners: List[str] = Field(default_factory=list)
    default_runner: str = "claude"


class OperatorRoutingPlan(BaseModel):
    query: str
    domain: str
    skill_match_id: Optional[str] = None
    target_agent: Optional[str] = None
    primary_runner: str
    fallback_runners: List[str] = Field(default_factory=list)
    cost_tier: CostTier
    is_approved: bool = True
    reason: str


class OperatorRouter:
    """Combines semantic/lexical routing with declarative policy to allocate the optimal runner for tasks."""

    def __init__(self, hybrid_router: HybridRouter, policy: Optional[OperatorRoutingPolicy] = None):
        self.hybrid = hybrid_router
        self.policy = policy or OperatorRoutingPolicy()

    def index(self, descriptors: List[SkillDescriptor]) -> None:
        self.hybrid.index(descriptors)

    def plan_routing(self, query: str, requested_runner: Optional[str] = None) -> OperatorRoutingPlan:
        hybrid_dec: HybridRoutingDecision = self.hybrid.route(query)
        domain = hybrid_dec.domain or "general"

        candidates = self.policy.domain_runners.get(domain, self.policy.domain_runners.get("general", ["claude"]))

        # Filter out disallowed runners
        allowed = [r for r in candidates if r not in self.policy.disallowed_runners]
        if not allowed:
            allowed = [self.policy.default_runner]

        # Check requested runner override
        if requested_runner:
            if requested_runner in self.policy.disallowed_runners:
                primary = allowed[0]
                reason = f"Requested runner '{requested_runner}' is blocked by policy; fell back to '{primary}'"
            elif requested_runner in allowed:
                primary = requested_runner
                allowed.remove(requested_runner)
                reason = f"Explicitly routed to requested runner '{primary}'"
            else:
                primary = requested_runner
                reason = f"Explicit override to '{primary}'"
        else:
            primary = allowed[0]
            allowed = allowed[1:]
            reason = f"Routed to '{primary}' via {hybrid_dec.routing_path} (domain: {domain})"

        return OperatorRoutingPlan(
            query=query,
            domain=domain,
            skill_match_id=hybrid_dec.match_id,
            target_agent=hybrid_dec.target_agent,
            primary_runner=primary,
            fallback_runners=allowed,
            cost_tier=hybrid_dec.cost_tier,
            is_approved=True,
            reason=reason,
        )
