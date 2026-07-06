import pytest
from vad.router.models import ModelInfo, RouteRequest, TokenBudget
from vad.router.budget import BudgetManager
from vad.router.providers.base import BaseProvider, DummyProvider
from vad.router.routing import Router


class EmptyProvider(BaseProvider):
    def get_model(self, name):
        return None

    def list_models(self):
        return []


class RegistryProvider(BaseProvider):
    def __init__(self):
        self.models = {
            "premium": ModelInfo(name="premium", tier="tier2", cost_per_1k_tokens=0.2, max_tokens=8192),
            "standard": ModelInfo(name="standard", tier="tier1", cost_per_1k_tokens=0.05, max_tokens=4096),
        }

    def get_model(self, name):
        return self.models.get(name)

    def list_models(self):
        return list(self.models.values())

def test_router_fallback_to_cheaper_model():
    provider = DummyProvider()
    router = Router(provider)
    
    # 0.05 budget is enough for cheap model (0.01 per 1k) but not expensive (0.1 per 1k)
    budget = BudgetManager(max_budget=0.05, max_depth=5)
    request = RouteRequest(task="test", max_budget=0.05, max_depth=5)
    
    selected_model = router.route(request, budget)
    
    assert selected_model == "cheap-model"
    assert ("route", "cheap-model") in router.log
    assert router.decisions[0].estimated_tokens == 1000
    assert router.decisions[0].estimated_cost == 0.01
    assert router.decisions[0].reason == "fallback due to budget"

def test_router_discovers_registered_models():
    router = Router(RegistryProvider())
    budget = BudgetManager(max_budget=0.1, max_depth=5)
    request = RouteRequest(task="test", max_budget=0.1, max_depth=5)

    selected_model = router.route(request, budget)

    assert selected_model == "standard"
    assert router.decisions[0].selected_model == "standard"
    assert router.evidence_events[0]["selected_model"] == "standard"
    assert router.evidence_events[0]["estimated_cost"] == 0.05
    assert router.evidence_events[0]["policy_decision"]["allow"] is True

def test_low_risk_formatting_selects_cheap_allowed_tier():
    router = Router(RegistryProvider())
    budget = BudgetManager(max_budget=1.0, max_depth=5)
    request = RouteRequest(task="format markdown", purpose="formatting", risk_tier="low", max_budget=1.0, max_depth=5)

    selected_model = router.route(request, budget)

    assert selected_model == "standard"
    assert router.decisions[0].selected_tier == "tier1"
    assert router.decisions[0].reason == "low-risk formatting uses cheapest allowed tier"

def test_high_risk_verification_requires_verifier_approved_tier():
    router = Router(RegistryProvider())
    budget = BudgetManager(max_budget=1.0, max_depth=5)
    request = RouteRequest(task="verify security fix", purpose="verification", risk_tier="high", max_budget=1.0, max_depth=5)

    selected_model = router.route(request, budget)

    assert selected_model == "premium"
    assert router.decisions[0].selected_tier == "tier2"
    assert router.decisions[0].reason == "verifier-approved tier required"

def test_disallowed_tier_is_denied_by_policy():
    class Tier3Provider(BaseProvider):
        def get_model(self, name):
            return None

        def list_models(self):
            return [ModelInfo(name="unsafe", tier="tier3", cost_per_1k_tokens=0.01, max_tokens=8192)]

    router = Router(Tier3Provider())
    budget = BudgetManager(max_budget=1.0, max_depth=5)
    request = RouteRequest(task="format markdown", purpose="formatting", risk_tier="low", max_budget=1.0, max_depth=5)

    with pytest.raises(ValueError, match="Token budget exhausted"):
        router.route(request, budget)
    assert router.evidence_events[0]["event"] == "route_denied"
    assert router.evidence_events[0]["selected_tier"] == "tier3"
    assert router.evidence_events[-1]["event"] == "route_exhausted"
    assert router.evidence_events[-1]["reason"] == "Token budget exhausted"

def test_router_blocks_when_no_models_registered():
    router = Router(EmptyProvider())
    budget = BudgetManager(max_budget=1.0, max_depth=5)
    request = RouteRequest(task="test", max_budget=1.0, max_depth=5)

    with pytest.raises(ValueError, match="No registered models available"):
        router.route(request, budget)

def test_router_exhausts_budget():
    provider = DummyProvider()
    router = Router(provider)
    
    # Very low budget
    budget = BudgetManager(max_budget=0.005, max_depth=5)
    request = RouteRequest(task="test", max_budget=0.005, max_depth=5)
    
    with pytest.raises(ValueError, match="Token budget exhausted"):
        router.route(request, budget)
    assert router.evidence_events[-1]["event"] == "route_exhausted"

def test_router_exhausts_depth():
    provider = DummyProvider()
    router = Router(provider)
    
    # Depth limit 1
    budget = BudgetManager(max_budget=1.0, max_depth=1)
    request = RouteRequest(task="test", max_budget=1.0, max_depth=1)
    
    # First call succeeds
    router.route(request, budget)
    
    # Second call exhausts depth
    with pytest.raises(ValueError, match="Loop depth exhausted"):
        router.route(request, budget)

def test_router_multi_iteration_task():
    provider = DummyProvider()
    router = Router(provider)
    
    budget = BudgetManager(max_budget=0.15, max_depth=5)
    request = RouteRequest(task="test", max_budget=0.15, max_depth=5)
    
    # Iteration 1: budget 0.15, expensive model costs 0.1, remaining 0.05
    model1 = router.route(request, budget)
    assert model1 == "expensive-model"
    
    # Iteration 2: budget 0.05, expensive model costs 0.1 -> fallback to cheap model costs 0.01
    model2 = router.route(request, budget)
    assert model2 == "cheap-model"
    
    # Iteration 3: budget 0.04
    model3 = router.route(request, budget)
    assert model3 == "cheap-model"
    
    log_actions = [x for _, x in router.log]
    assert log_actions == ["expensive-model", "cheap-model", "cheap-model"]


def test_token_budget_tracks_estimate_remaining_and_notes():
    budget = TokenBudget.create(1000, estimated=250, optimization_notes=["compact prompts"])

    assert budget.budget == 1000
    assert budget.estimated == 250
    assert budget.used == 0
    assert budget.remaining == 750
    assert budget.optimization_notes == ["compact prompts"]
    assert budget.approval_required is False


def test_token_budget_over_budget_requires_approval():
    estimated = TokenBudget.create(1000, estimated=1200)
    used = TokenBudget.create(1000).record_usage(1201)

    assert estimated.approval_required is True
    assert estimated.remaining == 0
    assert used.approval_required is True
    assert used.remaining == 0


def test_token_budget_usage_updates_remaining():
    budget = TokenBudget.create(1000, estimated=100).record_usage(300).record_usage(200)

    assert budget.used == 500
    assert budget.remaining == 500
    assert budget.approval_required is False
