"""Service layer for Hierarchical Intent 3-tier governance, tranche scheduling, and rollup release."""

from __future__ import annotations

from typing import Any, TypeAlias

from pydantic import BaseModel, Field

from vad.contracts.intent_hierarchy import (
    _DISPATCHABLE_OR_VERIFIED_STATUSES,
    _validate_parent_child_constraints,
    DecompositionStatus,
    EngineeringObjective,
    IntentLevel,
    Programme,
    StrategicObjective,
    approve_decomposition,
    compute_blocked_dependents,
    compute_execution_tranches,
    evaluate_objective_rollup,
    record_child_eip_verification,
    submit_decomposition_for_approval,
    validate_hierarchy_chain,
)

IntentHierarchyNode: TypeAlias = StrategicObjective | Programme | EngineeringObjective


class TrancheSchedulePlan(BaseModel):
    """Topological execution schedule and live dispatch readiness for an EngineeringObjective."""

    objective_id: str
    tranches: list[list[str]] = Field(default_factory=list)
    ready_to_dispatch: list[str] = Field(default_factory=list)
    blocked_dependents: dict[str, list[str]] = Field(default_factory=dict)
    completed_eips: list[str] = Field(default_factory=list)
    total_cost_usd: float = Field(default=0.0, ge=0.0)
    remaining_budget_usd: float | None = None


class ObjectiveReleaseReadiness(BaseModel):
    """Rollup verification and coordinated/decoupled release readiness summary."""

    objective_id: str
    rollup_verified: bool
    can_release_objective: bool
    releasable_child_eips: list[str] = Field(default_factory=list)
    held_in_staging_eips: list[str] = Field(default_factory=list)
    rollback_required_eips: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)


def _coerce_node(raw: Any) -> IntentHierarchyNode:
    """Coerce a stored dict or Pydantic model into a typed hierarchy node."""
    if isinstance(raw, (StrategicObjective, Programme, EngineeringObjective)):
        return raw
    if isinstance(raw, dict):
        level = raw.get("level")
        if level in (IntentLevel.STRATEGIC_OBJECTIVE, IntentLevel.STRATEGIC_OBJECTIVE.value):
            return StrategicObjective.model_validate(raw)
        if level in (IntentLevel.PROGRAMME, IntentLevel.PROGRAMME.value):
            return Programme.model_validate(raw)
        if level in (IntentLevel.ENGINEERING_OBJECTIVE, IntentLevel.ENGINEERING_OBJECTIVE.value):
            return EngineeringObjective.model_validate(raw)
    raise TypeError(f"Unsupported intent hierarchy node payload: {type(raw)!r}")


class IntentHierarchyService:
    """Manages 3-tier intent registration, approval gating, tranche planning, and rollup release."""

    def __init__(self, store: Any | None = None) -> None:
        self._store = store
        self._strategic: dict[str, StrategicObjective] = {}
        self._programmes: dict[str, Programme] = {}
        self._engineering: dict[str, EngineeringObjective] = {}

    def _has_store_save(self) -> bool:
        return self._store is not None and hasattr(self._store, "save_intent_hierarchy_node")

    def _cache_node(self, node: IntentHierarchyNode) -> None:
        if isinstance(node, StrategicObjective):
            self._strategic[node.node_id] = node
        elif isinstance(node, Programme):
            self._programmes[node.node_id] = node
        elif isinstance(node, EngineeringObjective):
            self._engineering[node.node_id] = node

    def _save_node(self, node: IntentHierarchyNode) -> None:
        self._cache_node(node)
        if self._has_store_save():
            self._store.save_intent_hierarchy_node(node)

    def _get_cached_node(self, node_id: str) -> IntentHierarchyNode | None:
        if node_id in self._strategic:
            return self._strategic[node_id]
        if node_id in self._programmes:
            return self._programmes[node_id]
        if node_id in self._engineering:
            return self._engineering[node_id]
        return None

    def _get_engineering_objective(self, objective_id: str) -> EngineeringObjective:
        node = self.get_node(objective_id)
        if not isinstance(node, EngineeringObjective):
            raise ValueError(
                f"Node '{objective_id}' has level '{node.level.value}', expected 'engineering_objective'"
            )
        return node

    def _resolve_parents(
        self, objective: EngineeringObjective
    ) -> tuple[StrategicObjective | None, Programme | None]:
        strategic: StrategicObjective | None = None
        programme: Programme | None = None

        if objective.parent_id is None:
            return strategic, programme

        parent = self.get_node(objective.parent_id)
        if isinstance(parent, Programme):
            programme = parent
            if programme.parent_id is not None:
                grandparent = self.get_node(programme.parent_id)
                if not isinstance(grandparent, StrategicObjective):
                    raise ValueError(
                        f"Programme '{programme.node_id}' parent '{programme.parent_id}' "
                        f"must be a StrategicObjective, got '{grandparent.level.value}'"
                    )
                strategic = grandparent
        elif isinstance(parent, StrategicObjective):
            strategic = parent
        else:
            raise ValueError(
                f"EngineeringObjective '{objective.node_id}' parent '{objective.parent_id}' "
                f"must be a Programme or StrategicObjective, got '{parent.level.value}'"
            )

        return strategic, programme

    def _sync_rollup_status(self, objective: EngineeringObjective) -> EngineeringObjective:
        rollup_ok, _ = evaluate_objective_rollup(objective)
        strategic, programme = self._resolve_parents(objective)
        chain_violations = validate_hierarchy_chain(strategic, programme, objective)
        if rollup_ok and not chain_violations:
            if objective.decomposition_status != DecompositionStatus.VERIFIED:
                return objective.model_copy(
                    update={"decomposition_status": DecompositionStatus.VERIFIED}
                )
            return objective
        if objective.decomposition_status == DecompositionStatus.VERIFIED:
            return objective.model_copy(
                update={"decomposition_status": DecompositionStatus.EXECUTING}
            )
        return objective

    def register_strategic_objective(self, node: StrategicObjective) -> StrategicObjective:
        """Register or update a top-level StrategicObjective."""
        if node.level != IntentLevel.STRATEGIC_OBJECTIVE:
            raise ValueError(
                f"StrategicObjective '{node.node_id}' has invalid level '{node.level.value}'"
            )
        self._save_node(node)
        return node

    def register_programme(self, node: Programme) -> Programme:
        """Register a Programme after validating floor, invariant, and budget nesting."""
        if node.level != IntentLevel.PROGRAMME:
            raise ValueError(f"Programme '{node.node_id}' has invalid level '{node.level.value}'")

        if node.parent_id is not None:
            parent = self.get_node(node.parent_id)
            if not isinstance(parent, StrategicObjective):
                raise ValueError(
                    f"Programme '{node.node_id}' parent '{node.parent_id}' "
                    f"must be a StrategicObjective, got '{parent.level.value}'"
                )
            violations = _validate_parent_child_constraints(
                parent,
                node,
                parent_label="StrategicObjective",
                child_label="Programme",
                check_parent_id=True,
            )
            if violations:
                raise ValueError(
                    f"Programme '{node.node_id}' violates parent constraints: "
                    f"{'; '.join(violations)}"
                )

        self._save_node(node)
        return node

    def register_engineering_objective(
        self, node: EngineeringObjective
    ) -> EngineeringObjective:
        """Register an EngineeringObjective after full 3-tier chain, floor, DAG, and budget checks."""
        if node.level != IntentLevel.ENGINEERING_OBJECTIVE:
            raise ValueError(
                f"EngineeringObjective '{node.node_id}' has invalid level '{node.level.value}'"
            )

        strategic, programme = self._resolve_parents(node)
        violations = validate_hierarchy_chain(strategic, programme, node)
        if violations:
            raise ValueError(
                f"EngineeringObjective '{node.node_id}' violates hierarchy constraints: "
                f"{'; '.join(violations)}"
            )

        self._save_node(node)
        return node

    def get_node(self, node_id: str) -> IntentHierarchyNode:
        """Return a registered intent node by ID, loading from store if configured."""
        cached = self._get_cached_node(node_id)
        if cached is not None:
            return cached

        if self._store is not None and hasattr(self._store, "load_intent_hierarchy_node"):
            loaded = self._store.load_intent_hierarchy_node(node_id)
            if loaded is not None:
                node = _coerce_node(loaded)
                self._cache_node(node)
                return node

        raise KeyError(f"Intent hierarchy node '{node_id}' not found")

    def list_nodes(self, *, level: IntentLevel | None = None) -> list[IntentHierarchyNode]:
        """List registered intent nodes, optionally filtered by IntentLevel."""
        by_id: dict[str, IntentHierarchyNode] = {}

        if self._store is not None and hasattr(self._store, "list_intent_hierarchy_nodes"):
            for raw in self._store.list_intent_hierarchy_nodes(level=level):
                node = _coerce_node(raw)
                self._cache_node(node)
                by_id[node.node_id] = node

        in_memory: list[IntentHierarchyNode] = [
            *self._strategic.values(),
            *self._programmes.values(),
            *self._engineering.values(),
        ]
        for node in in_memory:
            if (level is None or node.level == level) and node.node_id not in by_id:
                by_id[node.node_id] = node

        return list(by_id.values())

    def submit_for_approval(self, objective_id: str) -> EngineeringObjective:
        """Transition a validated EngineeringObjective decomposition to AWAITING_APPROVAL."""
        objective = self._get_engineering_objective(objective_id)
        updated = submit_decomposition_for_approval(objective)
        self._save_node(updated)
        return updated

    def approve_objective(
        self,
        objective_id: str,
        *,
        approver: str,
        approver_role: str = "release_guardian",
    ) -> EngineeringObjective:
        """Approve an EngineeringObjective decomposition after hierarchy and role checks."""
        objective = self._get_engineering_objective(objective_id)
        strategic, programme = self._resolve_parents(objective)
        violations = validate_hierarchy_chain(strategic, programme, objective)
        if violations:
            raise ValueError(
                f"Cannot approve EngineeringObjective '{objective_id}' with hierarchy "
                f"violations: {'; '.join(violations)}"
            )

        updated = approve_decomposition(
            objective,
            approver=approver,
            approver_role=approver_role,
        )
        self._save_node(updated)
        return updated

    def plan_execution_tranches(self, objective_id: str) -> TrancheSchedulePlan:
        """Compute topological execution tranches, ready leaves, blocked dependents, and budget."""
        objective = self._get_engineering_objective(objective_id)
        if objective.decomposition_status not in _DISPATCHABLE_OR_VERIFIED_STATUSES:
            raise PermissionError(
                f"Cannot plan execution tranches while decomposition_status is "
                f"'{objective.decomposition_status.value}'"
            )

        tranches = compute_execution_tranches(objective.child_eips)
        blocked_dependents = compute_blocked_dependents(objective.child_eips)
        children_by_name = {child.eip_name: child for child in objective.child_eips}

        completed_set = {
            child.eip_name
            for child in objective.child_eips
            if child.verified and child.evidence_fresh
        }
        completed_eips = [
            child.eip_name for child in objective.child_eips if child.eip_name in completed_set
        ]

        ready_to_dispatch: list[str] = []
        for tranche in tranches:
            for eip_name in tranche:
                child = children_by_name[eip_name]
                if (
                    eip_name not in completed_set
                    and eip_name not in blocked_dependents
                    and all(dep in completed_set for dep in child.depends_on)
                ):
                    ready_to_dispatch.append(eip_name)

        total_cost_usd = round(sum(child.cost_usd for child in objective.child_eips), 9)
        remaining_budget_usd = (
            None
            if objective.max_total_cost_usd is None
            else round(objective.max_total_cost_usd - total_cost_usd, 9)
        )

        return TrancheSchedulePlan(
            objective_id=objective.node_id,
            tranches=tranches,
            ready_to_dispatch=ready_to_dispatch,
            blocked_dependents=blocked_dependents,
            completed_eips=completed_eips,
            total_cost_usd=total_cost_usd,
            remaining_budget_usd=remaining_budget_usd,
        )

    def record_leaf_outcome(
        self,
        objective_id: str,
        *,
        eip_name: str,
        verified: bool,
        evidence_fresh: bool,
        evidence_digest: str | None = None,
        cost_usd: float | None = None,
    ) -> EngineeringObjective:
        """Record leaf EIP verification outcome and transition objective to VERIFIED if rollup passes."""
        objective = self._get_engineering_objective(objective_id)
        updated = record_child_eip_verification(
            objective,
            eip_name=eip_name,
            verified=verified,
            evidence_fresh=evidence_fresh,
            evidence_digest=evidence_digest,
            cost_usd=cost_usd,
        )
        updated = self._sync_rollup_status(updated)
        self._save_node(updated)
        return updated

    def record_cross_eip_proof(
        self,
        objective_id: str,
        *,
        passed: bool,
    ) -> EngineeringObjective:
        """Record cross-EIP integration proof result and re-evaluate objective rollup status."""
        objective = self._get_engineering_objective(objective_id)
        if objective.decomposition_status not in _DISPATCHABLE_OR_VERIFIED_STATUSES:
            raise PermissionError(
                f"Cannot record cross-EIP proof while decomposition_status is "
                f"'{objective.decomposition_status.value}'"
            )

        updated = objective.model_copy(update={"cross_eip_proof_passed": passed})
        updated = self._sync_rollup_status(updated)
        self._save_node(updated)
        return updated

    def evaluate_release_readiness(
        self,
        objective_id: str,
        *,
        failed_unrecoverably: list[str] | None = None,
    ) -> ObjectiveReleaseReadiness:
        """Evaluate coordinated or decoupled release readiness for an EngineeringObjective."""
        objective = self._get_engineering_objective(objective_id)
        rollup_ok, rollup_reasons = evaluate_objective_rollup(objective)
        strategic, programme = self._resolve_parents(objective)
        chain_violations = validate_hierarchy_chain(strategic, programme, objective)

        unrecoverable = list(dict.fromkeys(failed_unrecoverably or []))
        reasons = list(dict.fromkeys([*rollup_reasons, *chain_violations]))
        if unrecoverable:
            reasons.append(f"Unrecoverable child EIP failure(s): {', '.join(unrecoverable)}")

        rollup_verified = rollup_ok and not chain_violations and not unrecoverable

        if objective.require_coordinated_release:
            if rollup_verified:
                return ObjectiveReleaseReadiness(
                    objective_id=objective.node_id,
                    rollup_verified=True,
                    can_release_objective=True,
                    releasable_child_eips=[child.eip_name for child in objective.child_eips],
                    held_in_staging_eips=[],
                    rollback_required_eips=[],
                    reasons=reasons,
                )

            verified_children = [
                child.eip_name for child in objective.child_eips if child.verified
            ]
            has_unrecoverable = bool(unrecoverable) or (
                objective.decomposition_status == DecompositionStatus.FAILED
            )
            return ObjectiveReleaseReadiness(
                objective_id=objective.node_id,
                rollup_verified=False,
                can_release_objective=False,
                releasable_child_eips=[],
                held_in_staging_eips=[] if has_unrecoverable else verified_children,
                rollback_required_eips=verified_children if has_unrecoverable else [],
                reasons=reasons,
            )

        blocked = compute_blocked_dependents(objective.child_eips)
        unrecoverable_set = set(unrecoverable)
        can_dispatch = objective.decomposition_status in _DISPATCHABLE_OR_VERIFIED_STATUSES

        if can_dispatch:
            releasable_child_eips = [
                child.eip_name
                for child in objective.child_eips
                if (
                    child.verified
                    and child.evidence_fresh
                    and child.eip_name not in blocked
                    and child.eip_name not in unrecoverable_set
                )
            ]
        else:
            releasable_child_eips = []

        releasable_set = set(releasable_child_eips)
        held_in_staging_eips = [
            child.eip_name
            for child in objective.child_eips
            if (
                child.verified
                and child.eip_name not in releasable_set
                and child.eip_name not in unrecoverable_set
            )
        ]

        return ObjectiveReleaseReadiness(
            objective_id=objective.node_id,
            rollup_verified=rollup_verified,
            can_release_objective=rollup_verified,
            releasable_child_eips=releasable_child_eips,
            held_in_staging_eips=held_in_staging_eips,
            rollback_required_eips=[],
            reasons=reasons,
        )
