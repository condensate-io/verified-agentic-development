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


_APPROVER_ROLES = frozenset(
    {
        "release_guardian",
        "operator",
        "principal_architect",
        "programme_lead",
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


def _validate_parent_child_constraints(
    parent: IntentNode,
    child: IntentNode,
    *,
    parent_label: str,
    child_label: str,
    check_parent_id: bool = True,
) -> list[str]:
    """Check parent-child ID linkage, floors, invariants, and budget ceilings."""
    violations: list[str] = []

    if check_parent_id and child.parent_id != parent.node_id:
        violations.append(
            f"{child_label} '{child.node_id}' parent_id '{child.parent_id}' "
            f"does not match {parent_label} node_id '{parent.node_id}'"
        )

    if _RISK_RANK[child.risk_floor] < _RISK_RANK[parent.risk_floor]:
        violations.append(
            f"{child_label} '{child.node_id}' risk_floor '{child.risk_floor.value}' "
            f"downgrades below {parent_label} '{parent.node_id}' "
            f"risk_floor '{parent.risk_floor.value}'"
        )

    if _ASSURANCE_RANK[child.assurance_floor] < _ASSURANCE_RANK[parent.assurance_floor]:
        violations.append(
            f"{child_label} '{child.node_id}' assurance_floor "
            f"'{child.assurance_floor.value}' downgrades below {parent_label} "
            f"'{parent.node_id}' assurance_floor '{parent.assurance_floor.value}'"
        )

    child_invariants = set(child.inherited_invariants)
    missing_invariants = [
        inv
        for inv in dict.fromkeys(parent.inherited_invariants)
        if inv not in child_invariants
    ]
    if missing_invariants:
        violations.append(
            f"{child_label} '{child.node_id}' dropped inherited invariants from "
            f"{parent_label} '{parent.node_id}': {', '.join(missing_invariants)}"
        )

    if parent.max_total_cost_usd is not None:
        if child.max_total_cost_usd is None:
            violations.append(
                f"{child_label} '{child.node_id}' must specify max_total_cost_usd "
                f"<= {parent_label} '{parent.node_id}' ceiling "
                f"${parent.max_total_cost_usd:.2f}"
            )
        elif round(child.max_total_cost_usd, 9) > round(parent.max_total_cost_usd, 9):
            violations.append(
                f"{child_label} '{child.node_id}' max_total_cost_usd "
                f"${child.max_total_cost_usd:.2f} exceeds {parent_label} "
                f"'{parent.node_id}' ceiling ${parent.max_total_cost_usd:.2f}"
            )

    return violations


def validate_hierarchy_chain(
    strategic: StrategicObjective | None,
    programme: Programme | None,
    engineering: EngineeringObjective,
) -> list[str]:
    """Validate 3-tier hierarchy linkage, floors, invariants, budgets, and DAG targets."""
    violations: list[str] = []

    if strategic is not None and strategic.level != IntentLevel.STRATEGIC_OBJECTIVE:
        violations.append(
            f"StrategicObjective '{strategic.node_id}' has invalid level '{strategic.level.value}'"
        )
    if programme is not None and programme.level != IntentLevel.PROGRAMME:
        violations.append(
            f"Programme '{programme.node_id}' has invalid level '{programme.level.value}'"
        )
    if engineering.level != IntentLevel.ENGINEERING_OBJECTIVE:
        violations.append(
            f"EngineeringObjective '{engineering.node_id}' has invalid level '{engineering.level.value}'"
        )

    if strategic is not None and programme is not None:
        violations.extend(
            _validate_parent_child_constraints(
                strategic,
                programme,
                parent_label="StrategicObjective",
                child_label="Programme",
                check_parent_id=True,
            )
        )

    if programme is not None:
        violations.extend(
            _validate_parent_child_constraints(
                programme,
                engineering,
                parent_label="Programme",
                child_label="EngineeringObjective",
                check_parent_id=True,
            )
        )
    elif strategic is not None:
        violations.extend(
            _validate_parent_child_constraints(
                strategic,
                engineering,
                parent_label="StrategicObjective",
                child_label="EngineeringObjective",
                check_parent_id=False,
            )
        )

    violations.extend(validate_child_floor_inheritance(engineering))

    known_eips = {child.eip_name for child in engineering.child_eips}
    for child in engineering.child_eips:
        for dep in dict.fromkeys(child.depends_on):
            if dep not in known_eips:
                violations.append(
                    f"Child EIP '{child.eip_name}' depends_on unknown target '{dep}'"
                )

    return violations


def submit_decomposition_for_approval(
    objective: EngineeringObjective,
) -> EngineeringObjective:
    """Submit a validated EngineeringObjective decomposition for governance approval."""
    if objective.decomposition_status not in {
        DecompositionStatus.DRAFT,
        DecompositionStatus.AWAITING_APPROVAL,
    }:
        raise ValueError(
            f"Cannot submit decomposition from status '{objective.decomposition_status.value}'; "
            "must be 'draft' or 'awaiting_approval'"
        )
    if not objective.child_eips:
        raise ValueError("Cannot submit decomposition with no child EIPs")

    violations = validate_child_floor_inheritance(objective)
    if violations:
        raise ValueError(
            f"Cannot submit decomposition with validation violations: {'; '.join(violations)}"
        )

    return objective.model_copy(
        update={
            "decomposition_status": DecompositionStatus.AWAITING_APPROVAL,
            "approved_by": None,
        }
    )


def approve_decomposition(
    objective: EngineeringObjective,
    *,
    approver: str,
    approver_role: str = "release_guardian",
) -> EngineeringObjective:
    """Approve an EngineeringObjective decomposition after role and floor checks."""
    normalized_approver = approver.strip()
    normalized_role = approver_role.strip().lower()
    if not normalized_approver or normalized_role not in _APPROVER_ROLES:
        raise PermissionError(
            f"Unauthorized decomposition approval by '{approver}' with role '{approver_role}'"
        )

    if not objective.child_eips:
        raise ValueError("Cannot approve decomposition with no child EIPs")

    violations = validate_child_floor_inheritance(objective)
    if violations:
        raise ValueError(
            f"Cannot approve decomposition with validation violations: {'; '.join(violations)}"
        )

    return objective.model_copy(
        update={
            "decomposition_status": DecompositionStatus.APPROVED,
            "approved_by": normalized_approver,
        }
    )


def record_child_eip_verification(
    objective: EngineeringObjective,
    *,
    eip_name: str,
    verified: bool,
    evidence_fresh: bool,
    evidence_digest: str | None = None,
    cost_usd: float | None = None,
) -> EngineeringObjective:
    """Record verification status and freshness for a child EIP within an objective."""
    if objective.decomposition_status not in _DISPATCHABLE_OR_VERIFIED_STATUSES:
        raise PermissionError(
            f"Cannot record verification while decomposition_status is "
            f"'{objective.decomposition_status.value}'"
        )

    if cost_usd is not None and cost_usd < 0.0:
        raise ValueError("cost_usd must be non-negative")

    found = False
    updated_children: list[ChildEIPRef] = []
    for child in objective.child_eips:
        if child.eip_name == eip_name:
            found = True
            updates: dict[str, object] = {
                "verified": verified,
                "evidence_fresh": evidence_fresh,
            }
            if evidence_digest is not None:
                updates["evidence_digest"] = evidence_digest
            if cost_usd is not None:
                updates["cost_usd"] = cost_usd
            updated_children.append(child.model_copy(update=updates))
        else:
            updated_children.append(child)

    if not found:
        raise KeyError(
            f"Child EIP '{eip_name}' not found in EngineeringObjective '{objective.node_id}'"
        )

    next_status = (
        DecompositionStatus.EXECUTING
        if objective.decomposition_status == DecompositionStatus.APPROVED
        else objective.decomposition_status
    )

    return objective.model_copy(
        update={
            "decomposition_status": next_status,
            "child_eips": updated_children,
        }
    )


def compute_blocked_dependents(child_eips: list[ChildEIPRef]) -> dict[str, list[str]]:
    """Return transitive downstream child EIPs blocked by unverified or stale ancestors."""
    if not child_eips:
        return {}

    known_names = {child.eip_name for child in child_eips}
    dependents: dict[str, list[str]] = {child.eip_name: [] for child in child_eips}
    for child in child_eips:
        for dep in dict.fromkeys(child.depends_on):
            if dep in known_names and dep != child.eip_name:
                dependents[dep].append(child.eip_name)

    unhealthy = [
        child.eip_name
        for child in child_eips
        if not (child.verified and child.evidence_fresh)
    ]

    blocked_by: dict[str, list[str]] = {}
    for blocker in unhealthy:
        visited: set[str] = {blocker}
        queue: list[str] = list(dependents[blocker])
        while queue:
            current = queue.pop(0)
            if current in visited:
                continue
            visited.add(current)
            blocked_by.setdefault(current, []).append(blocker)
            queue.extend(dependents[current])

    return {
        child.eip_name: blocked_by[child.eip_name]
        for child in child_eips
        if child.eip_name in blocked_by
    }
