from pydantic import BaseModel, Field
from typing import Dict

class ModelInfo(BaseModel):
    name: str
    tier: str = "tier1"
    cost_per_1k_tokens: float
    max_tokens: int

class RouteRequest(BaseModel):
    task: str
    max_budget: float
    max_depth: int
    risk_tier: str = "low"
    purpose: str = "general"
    current_depth: int = 0
    metadata: Dict[str, str] = Field(default_factory=dict)


class TokenBudget(BaseModel):
    budget: int = Field(ge=0)
    estimated: int = Field(default=0, ge=0)
    used: int = Field(default=0, ge=0)
    remaining: int = Field(ge=0)
    optimization_notes: list[str] = Field(default_factory=list)
    approval_required: bool = False

    @classmethod
    def create(cls, budget: int, estimated: int = 0, optimization_notes: list[str] | None = None) -> "TokenBudget":
        remaining = max(0, budget - estimated)
        return cls(
            budget=budget,
            estimated=estimated,
            used=0,
            remaining=remaining,
            optimization_notes=optimization_notes or [],
            approval_required=estimated > budget,
        )

    def record_usage(self, used: int) -> "TokenBudget":
        total_used = self.used + used
        remaining = max(0, self.budget - total_used)
        return TokenBudget(
            budget=self.budget,
            estimated=self.estimated,
            used=total_used,
            remaining=remaining,
            optimization_notes=self.optimization_notes,
            approval_required=total_used > self.budget,
        )


class RouteDecision(BaseModel):
    selected_model: str
    selected_tier: str
    estimated_tokens: int = Field(ge=0)
    estimated_cost: float = Field(ge=0)
    reason: str


class RouteEvidenceEvent(BaseModel):
    event: str = "route_decision"
    selected_model: str | None = None
    selected_tier: str | None = None
    estimated_tokens: int = Field(default=0, ge=0)
    estimated_cost: float = Field(default=0, ge=0)
    reason: str
    policy_decision: dict | None = None
