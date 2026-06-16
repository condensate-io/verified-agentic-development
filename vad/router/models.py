from pydantic import BaseModel, Field
from typing import Dict

class ModelInfo(BaseModel):
    name: str
    cost_per_1k_tokens: float
    max_tokens: int

class RouteRequest(BaseModel):
    task: str
    max_budget: float
    max_depth: int
    current_depth: int = 0
    metadata: Dict[str, str] = Field(default_factory=dict)
