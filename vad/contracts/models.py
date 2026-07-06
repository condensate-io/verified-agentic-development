from typing import List, Optional
from enum import Enum
from pydantic import BaseModel, Field, model_validator

class RiskTier(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"

class AutonomyTier(str, Enum):
    MANUAL = "manual"
    ASSISTED = "assisted"
    BOUNDED = "bounded"
    AUTONOMOUS = "autonomous"

class ProofKind(str, Enum):
    UNIT = "unit"
    PROPERTY = "property"
    CONTRACT = "contract"
    SECURITY = "security"
    PERFORMANCE = "performance"
    POLICY = "policy"
    RELEASE = "release"
    TELEMETRY = "telemetry"
    MANUAL_GATE = "manual_gate"

class Goal(BaseModel):
    description: str
    success_criteria: List[str]

class ProofObligation(BaseModel):
    id: str
    kind: ProofKind
    description: str

class Invariants(BaseModel):
    security: List[str] = Field(default_factory=list)
    performance: List[str] = Field(default_factory=list)
    functional: List[str] = Field(default_factory=list)

class Constraints(BaseModel):
    security: List[str] = Field(default_factory=list)
    privacy: List[str] = Field(default_factory=list)
    operational: List[str] = Field(default_factory=list)

class ToolPermissions(BaseModel):
    allowed: List[str] = Field(default_factory=list)
    denied: List[str] = Field(default_factory=list)

class MemoryRequirement(BaseModel):
    scope: str
    purpose: str
    max_payload_size: int = Field(default=1024, ge=1)

class ModelBudget(BaseModel):
    max_tokens: int = Field(ge=0)
    max_cost: float = Field(ge=0)
    max_loop_depth: int = Field(ge=1)

class ReleaseRequirements(BaseModel):
    required: bool
    strategy: Optional[str] = None
    gates: List[str] = Field(default_factory=list)

class TelemetryRequirements(BaseModel):
    required: bool
    signals: List[str] = Field(default_factory=list)

class EIP(BaseModel):
    version: str = Field(..., pattern=r"^\d+\.\d+\.\d+$")
    name: str
    goal: Goal
    non_goals: List[str]
    risk_tier: RiskTier
    autonomy_tier: AutonomyTier
    scope_boundaries: List[str]
    invariants: Invariants
    constraints: Constraints
    proof_obligations: List[ProofObligation]
    tool_permissions: ToolPermissions
    memory_requirements: List[MemoryRequirement]
    model_budget: ModelBudget
    release_requirements: ReleaseRequirements
    telemetry_requirements: TelemetryRequirements

    @model_validator(mode="after")
    def require_telemetry_for_release(self):
        if self.release_requirements.required and not self.telemetry_requirements.required:
            raise ValueError("release requires telemetry requirements")
        return self
