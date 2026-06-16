import pytest
from vad.router.models import RouteRequest
from vad.router.budget import BudgetManager
from vad.router.providers.base import DummyProvider
from vad.router.routing import Router

def test_router_fallback_to_cheaper_model():
    provider = DummyProvider()
    router = Router(provider)
    
    # 0.05 budget is enough for cheap model (0.01 per 1k) but not expensive (0.1 per 1k)
    budget = BudgetManager(max_budget=0.05, max_depth=5)
    request = RouteRequest(task="test", max_budget=0.05, max_depth=5)
    
    selected_model = router.route(request, budget)
    
    assert selected_model == "cheap-model"
    assert ("route", "cheap-model") in router.log

def test_router_exhausts_budget():
    provider = DummyProvider()
    router = Router(provider)
    
    # Very low budget
    budget = BudgetManager(max_budget=0.005, max_depth=5)
    request = RouteRequest(task="test", max_budget=0.005, max_depth=5)
    
    with pytest.raises(ValueError, match="Token budget exhausted"):
        router.route(request, budget)

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
