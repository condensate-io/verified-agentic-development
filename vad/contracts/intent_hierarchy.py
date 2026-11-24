"""Prototype contracts and rollup governance for Hierarchical Intent Models."""

from enum import Enum
from pydantic import BaseModel, Field

from vad.contracts.models import AssuranceLevel, RiskTier

_RISK_RANK: dict[RiskTier, int] = {
    RiskTier.LOW: 1,
    RiskTier.MEDIUM: 2,
    RiskTier.HIGH: 3,
    RiskTier.CRITICAL: 4,
}

_ASSURANCE_RANK: dict[AssuranceLevel, int] = {
    AssuranceLevel.A1_BASIC: 1,
    AssuranceLevel.A2_INDEPENDENT: 2,
    AssuranceLevel.A3_ATTESTED: 3,
    AssuranceLevel.A4_RESTRICTED: 4,
}


class IntentLevel(str, Enum):
    STRATEGIC_OBJECTIVE = "strategic_objective"
    PROGRAMME = "programme"
    ENGINEERING_OBJECTIVE = "engineering_objective"
    EIP = "eip"
    TASK = "task"
    PROOF_OBLIGATION = "proof_obligation"
    EVIDENCE = "evidence"


class DecompositionStatus(str, Enum):
    DRAFT = "draft"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    EXECUTING = "executing"
    VERIFIED = "verified"
    FAILED = "failed"
    CANCELLED = "cancelled"


_DISPATCHABLE_OR_VERIFIED_STATUSES = frozenset(
    {
        DecompositionStatus.APPROVED,
        DecompositionStatus.EXECUTING,
        DecompositionStatus.VERIFIED,
    }
)


class ChildEIPRef(BaseModel):
    """Reference to an atomic leaf EIP within an EngineeringObjective DAG."""

    eip_name: str
    eip_digest: str
    risk_tier: RiskTier
    assurance_level: AssuranceLevel
    depends_on: list[str] = Field(default_factory=list)
    verified: bool = False
    evidence_fresh: bool = False
    evidence_digest: str | None = None
    cost_usd: float = Field(default=0.0, ge=0.0)


class IntentNode(BaseModel):
    """Base governance node in the VAD hierarchical intent model."""

    node_id: str
    level: IntentLevel
    title: str
    description: str
    parent_id: str | None = None
    risk_floor: RiskTier = RiskTier.LOW
    assurance_floor: AssuranceLevel = AssuranceLevel.A1_BASIC
    inherited_invariants: list[str] = Field(default_factory=list)
    max_total_cost_usd: float | None = Field(default=None, ge=0.0)
    decomposition_status: DecompositionStatus = DecompositionStatus.DRAFT
    approved_by: str | None = None


class StrategicObjective(IntentNode):
    """Top-level strategic outcome governing programmes and risk/invariant floors."""

    level: IntentLevel = IntentLevel.STRATEGIC_OBJECTIVE


class Programme(IntentNode):
    """Coordinated multi-objective programme beneath a StrategicObjective."""

    level: IntentLevel = IntentLevel.PROGRAMME


class EngineeringObjective(IntentNode):
    """Decomposable engineering objective backed by a DAG of atomic child EIPs."""

    level: IntentLevel = IntentLevel.ENGINEERING_OBJECTIVE
    child_eips: list[ChildEIPRef] = Field(default_factory=list)
    cross_eip_proof_passed: bool = False
    require_coordinated_release: bool = False


def compute_execution_tranches(child_eips: list[ChildEIPRef]) -> list[list[str]]:
    """Group child EIPs into parallel topological execution layers.

    Raises ValueError if duplicate eip_name entries or cyclic dependencies exist.
    """
    if not child_eips:
        return []

    nodes_by_name: dict[str, ChildEIPRef] = {}
    for child in child_eips:
        if child.eip_name in nodes_by_name:
            raise ValueError(f"Duplicate child EIP name '{child.eip_name}' in objective")
        nodes_by_name[child.eip_name] = child

    in_degree: dict[str, int] = {child.eip_name: 0 for child in child_eips}
    dependents: dict[str, list[str]] = {child.eip_name: [] for child in child_eips}

    for child in child_eips:
        for dep in dict.fromkeys(child.depends_on):
            if dep == child.eip_name:
                raise ValueError(
                    f"Cyclic dependency detected: '{child.eip_name}' depends on itself"
                )
            if dep in nodes_by_name:
                dependents[dep].append(child.eip_name)
                in_degree[child.eip_name] += 1

    ready = [child.eip_name for child in child_eips if in_degree[child.eip_name] == 0]
    tranches: list[list[str]] = []
    visited = 0

    while ready:
        tranches.append(ready)
        visited += len(ready)
        next_ready_set: set[str] = set()
        for name in ready:
            for dependent in dependents[name]:
                in_degree[dependent] -= 1
                if in_degree[dependent] == 0:
                    next_ready_set.add(dependent)
        ready = [child.eip_name for child in child_eips if child.eip_name in next_ready_set]

    if visited != len(child_eips):
        cyclic_nodes = [
            child.eip_name for child in child_eips if in_degree[child.eip_name] > 0
        ]
        raise ValueError(
            f"Cyclic dependency detected among child EIPs: {', '.join(cyclic_nodes)}"
        )

    return tranches


def validate_child_floor_inheritance(objective: EngineeringObjective) -> list[str]:
    """Validate non-downgradable floors, DAG acyclicity, and aggregate budget ceilings."""
    violations: list[str] = []

    for child in objective.child_eips:
        if _RISK_RANK[child.risk_tier] < _RISK_RANK[objective.risk_floor]:
            violations.append(
                f"Child EIP '{child.eip_name}' risk_tier '{child.risk_tier.value}' "
                f"downgrades below objective risk_floor '{objective.risk_floor.value}'"
            )
        if _ASSURANCE_RANK[child.assurance_level] < _ASSURANCE_RANK[objective.assurance_floor]:
            violations.append(
                f"Child EIP '{child.eip_name}' assurance_level "
                f"'{child.assurance_level.value}' downgrades below objective "
                f"assurance_floor '{objective.assurance_floor.value}'"
            )

    try:
        compute_execution_tranches(objective.child_eips)
    except ValueError as exc:
        violations.append(str(exc))

    if objective.max_total_cost_usd is not None:
        total_cost = sum(child.cost_usd for child in objective.child_eips)
        if round(total_cost, 9) > round(objective.max_total_cost_usd, 9):
            violations.append(
                f"Total child EIP cost ${total_cost:.2f} exceeds objective "
                f"max_total_cost_usd ${objective.max_total_cost_usd:.2f}"
            )

    return violations


def evaluate_objective_rollup(objective: EngineeringObjective) -> tuple[bool, list[str]]:
    """Evaluate whether an EngineeringObjective satisfies full rollup verification."""
    reasons: list[str] = []

    if objective.decomposition_status not in _DISPATCHABLE_OR_VERIFIED_STATUSES:
        reasons.append(
            f"Objective decomposition_status '{objective.decomposition_status.value}' "
            "must be approved, executing, or verified"
        )

    reasons.extend(validate_child_floor_inheritance(objective))

    if not objective.child_eips:
        reasons.append("Objective has no child EIPs to verify")

    for child in objective.child_eips:
        if not child.verified:
            reasons.append(f"Child EIP '{child.eip_name}' is not verified")
        if not child.evidence_fresh:
            reasons.append(f"Child EIP '{child.eip_name}' does not have fresh evidence")

    if not objective.cross_eip_proof_passed:
        reasons.append("Cross-EIP integration proof obligations have not passed")

    return len(reasons) == 0, reasons
