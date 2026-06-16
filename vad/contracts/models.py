from typing import List
from enum import Enum
from pydantic import BaseModel, Field

class RiskTier(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"

class Goal(BaseModel):
    description: str
    success_criteria: List[str]

class ProofObligation(BaseModel):
    id: str
    kind: str
    description: str

class Invariants(BaseModel):
    security: List[str] = []
    performance: List[str] = []
    functional: List[str] = []

class EIP(BaseModel):
    version: str = Field(..., pattern=r"^\d+\.\d+\.\d+$")
    name: str
    risk_tier: RiskTier
    goal: Goal
    invariants: Invariants
    proof_obligations: List[ProofObligation]
