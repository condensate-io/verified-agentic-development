import logging
from typing import List, Tuple
from vad.router.models import RouteDecision, RouteEvidenceEvent, RouteRequest
from vad.router.budget import BudgetManager
from vad.router.providers.base import BaseProvider
from vad.policy.engine import PolicyEngine

logger = logging.getLogger(__name__)

class Router:
    def __init__(self, provider: BaseProvider, policy_path: str = "policies/vad.rego"):
        self.provider = provider
        self.policy_path = policy_path
        self.policy = PolicyEngine()
        self.log: List[Tuple[str, str]] = []  # Logs actions for tests
        self.decisions: list[RouteDecision] = []
        self.evidence_events: list[dict] = []

    def route(self, request: RouteRequest, budget: BudgetManager) -> str:
        budget.increment_depth()

        # Mock token estimation
        expected_tokens = 1000
        candidates = self._ordered_candidates(request)
        if not candidates:
            raise ValueError("No registered models available")

        for index, model in enumerate(candidates):
            decision = self.policy.evaluate_decision(self.policy_path, {"action": "use_model", "model_tier": model.tier})
            if not decision.allow:
                self.evidence_events.append(RouteEvidenceEvent(
                    event="route_denied",
                    selected_model=model.name,
                    selected_tier=model.tier,
                    reason="model tier denied by policy",
                    policy_decision=decision.model_dump(mode="json"),
                ).model_dump(mode="json"))
                continue
            estimated_cost = (expected_tokens / 1000) * model.cost_per_1k_tokens
            if budget.spent + estimated_cost <= budget.max_budget:
                budget.charge(estimated_cost)
                self.log.append(("route", model.name))
                reason = _route_reason(request, index)
                self.decisions.append(RouteDecision(
                    selected_model=model.name,
                    selected_tier=model.tier,
                    estimated_tokens=expected_tokens,
                    estimated_cost=estimated_cost,
                    reason=reason,
                ))
                self.evidence_events.append(RouteEvidenceEvent(
                    selected_model=model.name,
                    selected_tier=model.tier,
                    estimated_tokens=expected_tokens,
                    estimated_cost=estimated_cost,
                    reason=reason,
                    policy_decision=decision.model_dump(mode="json"),
                ).model_dump(mode="json"))
                if index > 0:
                    logger.info("Falling back to cheaper model due to budget.")
                return model.name

        self.evidence_events.append(RouteEvidenceEvent(event="route_exhausted", reason="Token budget exhausted").model_dump(mode="json"))
        raise ValueError("Token budget exhausted")

    def _ordered_candidates(self, request: RouteRequest):
        models = self.provider.list_models()
        if request.risk_tier == "high" or request.purpose == "verification":
            required_tiers = {"tier2"}
            eligible = [model for model in models if model.tier in required_tiers]
            return sorted(eligible, key=lambda model: model.cost_per_1k_tokens, reverse=True)
        if request.purpose == "formatting":
            return sorted(models, key=lambda model: model.cost_per_1k_tokens)
        return sorted(models, key=lambda model: model.cost_per_1k_tokens, reverse=True)


def _route_reason(request: RouteRequest, index: int) -> str:
    if request.risk_tier == "high" or request.purpose == "verification":
        return "verifier-approved tier required"
    if request.purpose == "formatting":
        return "low-risk formatting uses cheapest allowed tier"
    return "within budget" if index == 0 else "fallback due to budget"
