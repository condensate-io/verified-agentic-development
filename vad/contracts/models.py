from typing import List, Optional
from enum import Enum
from pydantic import BaseModel, Field, model_validator

class RiskTier(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class AssuranceLevel(str, Enum):
    A1_BASIC = "A1_BASIC"
    A2_INDEPENDENT = "A2_INDEPENDENT"
    A3_ATTESTED = "A3_ATTESTED"
    A4_RESTRICTED = "A4_RESTRICTED"


_ASSURANCE_RANK = {
    AssuranceLevel.A1_BASIC: 1,
    AssuranceLevel.A2_INDEPENDENT: 2,
    AssuranceLevel.A3_ATTESTED: 3,
    AssuranceLevel.A4_RESTRICTED: 4,
}

_RISK_DEFAULT_ASSURANCE = {
    RiskTier.LOW: AssuranceLevel.A1_BASIC,
    RiskTier.MEDIUM: AssuranceLevel.A2_INDEPENDENT,
    RiskTier.HIGH: AssuranceLevel.A3_ATTESTED,
    RiskTier.CRITICAL: AssuranceLevel.A4_RESTRICTED,
}


def default_assurance_for_risk(risk_tier: RiskTier) -> AssuranceLevel:
    """Return the default assurance level required for a given risk tier."""
    return _RISK_DEFAULT_ASSURANCE[risk_tier]


class FreshnessPolicy(BaseModel):
    max_evidence_age_seconds: int = Field(default=14400, ge=1)
    require_head_commit_match: bool = True
    require_tree_hash_match: bool = True
    require_env_digest_match: bool = False
    invalidate_on_dependency_change: bool = True


_RISK_DEFAULT_FRESHNESS_PARAMS = {
    RiskTier.LOW: {
        "max_evidence_age_seconds": 86400,
        "require_tree_hash_match": False,
        "require_env_digest_match": False,
    },
    RiskTier.MEDIUM: {
        "max_evidence_age_seconds": 14400,
        "require_tree_hash_match": True,
        "require_env_digest_match": False,
    },
    RiskTier.HIGH: {
        "max_evidence_age_seconds": 3600,
        "require_tree_hash_match": True,
        "require_env_digest_match": True,
    },
    RiskTier.CRITICAL: {
        "max_evidence_age_seconds": 900,
        "require_tree_hash_match": True,
        "require_env_digest_match": True,
    },
}


def default_freshness_for_risk(risk_tier: RiskTier) -> FreshnessPolicy:
    """Return the default freshness policy for a given risk tier."""
    return FreshnessPolicy(**_RISK_DEFAULT_FRESHNESS_PARAMS[risk_tier])

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
    min_assurance_level: Optional[AssuranceLevel] = None
    independent_execution_required: bool = False

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
    assurance_level: Optional[AssuranceLevel] = None
    freshness_policy: Optional[FreshnessPolicy] = None

    def effective_assurance_level(self) -> AssuranceLevel:
        """Return explicit assurance level or the default for this EIP's risk tier."""
        return self.assurance_level or default_assurance_for_risk(self.risk_tier)

    def effective_freshness_policy(self) -> FreshnessPolicy:
        """Return explicit freshness policy or the default for this EIP's risk tier."""
        return self.freshness_policy or default_freshness_for_risk(self.risk_tier)

    @model_validator(mode="after")
    def require_telemetry_for_release(self):
        if self.release_requirements.required and not self.telemetry_requirements.required:
            raise ValueError("release requires telemetry requirements")
        return self

    @model_validator(mode="after")
    def forbid_critical_assurance_downgrade(self):
        if (
            self.risk_tier == RiskTier.CRITICAL
            and self.assurance_level is not None
            and _ASSURANCE_RANK[self.assurance_level] < _ASSURANCE_RANK[AssuranceLevel.A4_RESTRICTED]
        ):
            raise ValueError("critical risk_tier cannot downgrade assurance_level below A4_RESTRICTED")
        return self
