import logging

logger = logging.getLogger(__name__)

class BudgetManager:
    def __init__(self, max_budget: float, max_depth: int):
        self.max_budget = max_budget
        self.max_depth = max_depth
        self.spent = 0.0
        self.current_depth = 0

    def charge(self, amount: float):
        self.spent += amount
        if self.spent > self.max_budget:
            logger.warning(f"Budget exhausted! Spent: {self.spent}, Max: {self.max_budget}")
            raise ValueError("Token budget exhausted")

    def increment_depth(self):
        self.current_depth += 1
        if self.current_depth > self.max_depth:
            logger.warning(f"Depth limit reached! Depth: {self.current_depth}, Max: {self.max_depth}")
            raise ValueError("Loop depth exhausted")
