import logging
from typing import List, Tuple
from vad.router.models import RouteRequest
from vad.router.budget import BudgetManager
from vad.router.providers.base import BaseProvider

logger = logging.getLogger(__name__)

class Router:
    def __init__(self, provider: BaseProvider):
        self.provider = provider
        self.log: List[Tuple[str, str]] = []  # Logs actions for tests

    def route(self, request: RouteRequest, budget: BudgetManager) -> str:
        budget.increment_depth()
        expensive = self.provider.get_model("expensive-model")
        cheap = self.provider.get_model("cheap-model")

        # Mock token estimation
        expected_tokens = 1000 
        
        if expensive:
            expensive_cost = (expected_tokens / 1000) * expensive.cost_per_1k_tokens
            if budget.spent + expensive_cost <= budget.max_budget:
                budget.charge(expensive_cost)
                self.log.append(("route", "expensive-model"))
                return "expensive-model"
                
        if cheap:
            cheap_cost = (expected_tokens / 1000) * cheap.cost_per_1k_tokens
            if budget.spent + cheap_cost <= budget.max_budget:
                budget.charge(cheap_cost)
                self.log.append(("route", "cheap-model"))
                logger.info("Falling back to cheaper model due to budget.")
                return "cheap-model"
        
        raise ValueError("Token budget exhausted")
