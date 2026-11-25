"""Hierarchical multi-EIP delivery workflow orchestrating topological DAG tranches."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from pydantic import BaseModel, Field

from vad.contracts.intent_hierarchy import (
    EngineeringObjective,
    evaluate_objective_rollup,
    validate_child_floor_inheritance,
)
from vad.contracts.models import RiskTier
from vad.control_plane.intent_hierarchy_service import (
    IntentHierarchyService,
    ObjectiveReleaseReadiness,
)
from vad.memory.gateway import MemoryGateway
from vad.policy.autonomy_matrix import PolicyStage
from vad.server.db.store import ServerStore
from vad.signing.ed25519_local import LocalEd25519KeyProvider
from vad.workflows.verified_delivery import (
    VerifiedDeliveryRequest,
    VerifiedDeliveryResult,
    VerifiedDeliveryWorkflow,
)


class ChildEIPExecutionSpec(BaseModel):
    """Execution parameters for a single child EIP within an EngineeringObjective."""

    eip_name: str
    change_request: str
    risk_tier: RiskTier | None = None
    domain_tags: list[str] = Field(default_factory=list)
    builder_actor: str = "codex-builder-1"
    builder_host_type: str = "codex"
    verifier_actor: str = "isolated-verifier-1"
    human_approvers: list[str] = Field(default_factory=list)
    explicit_authorization_ref: str | None = None
    commit_sha: str = "a" * 40
    stage: PolicyStage = PolicyStage.CI_CD
    cost_usd: float | None = None
    simulate_drift_after_verify: bool = False
    simulate_unrecoverable_failure: bool = False


class HierarchicalDeliveryRequest(BaseModel):
    """Input specification for executing an EngineeringObjective DAG of child EIPs."""

    objective_id: str
    run_id_prefix: str = "obj-run"
    child_specs: dict[str, ChildEIPExecutionSpec] = Field(default_factory=dict)
    cross_eip_proof_callback: Any | None = None
    run_cross_eip_proof: bool = True


class HierarchicalDeliveryResult(BaseModel):
    """Structured outcome from executing an EngineeringObjective across topological tranches."""

    objective_id: str
    executed_tranches: list[list[str]]
    child_results: dict[str, VerifiedDeliveryResult]
    child_run_ids: dict[str, str]
    skipped_blocked_eips: dict[str, list[str]]
    cross_eip_proof_passed: bool
    rollup_verified: bool
    rollup_reasons: list[str]
    release_readiness: ObjectiveReleaseReadiness
    final_objective: EngineeringObjective


class HierarchicalDeliveryWorkflow:
    """Orchestrates approved EngineeringObjective decompositions across topological tranches."""

    def __init__(
        self,
        store: ServerStore,
        *,
        hierarchy_service: IntentHierarchyService | None = None,
        memory_gateway: MemoryGateway | None = None,
        key_provider: LocalEd25519KeyProvider | None = None,
        workspace_dir: Path | None = None,
        command_executor: Callable | None = None,
    ) -> None:
        self.store = store
        self.hierarchy_service = (
            hierarchy_service
            if hierarchy_service is not None
            else IntentHierarchyService(store=store)
        )
        self.leaf_workflow = VerifiedDeliveryWorkflow(
            store=store,
            memory_gateway=memory_gateway,
            key_provider=key_provider,
            workspace_dir=workspace_dir,
            command_executor=command_executor,
        )

    def _load_engineering_objective(self, objective_id: str) -> EngineeringObjective:
        node = self.hierarchy_service.get_node(objective_id)
        if not isinstance(node, EngineeringObjective):
            raise ValueError(
                f"Node '{objective_id}' has level '{node.level.value}', "
                "expected 'engineering_objective'"
            )
        return node

    def execute(self, request: HierarchicalDeliveryRequest) -> HierarchicalDeliveryResult:
        """Execute an approved EngineeringObjective's child EIPs in topological order."""
        objective = self._load_engineering_objective(request.objective_id)
        schedule = self.hierarchy_service.plan_execution_tranches(request.objective_id)

        violations = validate_child_floor_inheritance(objective)
        if violations:
            raise ValueError(
                f"EngineeringObjective '{request.objective_id}' violates floor/budget "
                f"constraints: {'; '.join(violations)}"
            )

        executed_tranches: list[list[str]] = []
        child_results: dict[str, VerifiedDeliveryResult] = {}
        child_run_ids: dict[str, str] = {}
        skipped_blocked_eips: dict[str, list[str]] = {}
        unrecoverable_failures: list[str] = []
        cumulative_cost_usd = 0.0

        for tranche in schedule.tranches:
            tranche_executed: list[str] = []
            children_by_name = {child.eip_name: child for child in objective.child_eips}

            for eip_name in tranche:
                child_ref = children_by_name[eip_name]
                blocking_deps = [
                    dep
                    for dep in dict.fromkeys(child_ref.depends_on)
                    if dep not in children_by_name
                    or not (
                        children_by_name[dep].verified
                        and children_by_name[dep].evidence_fresh
                    )
                ]
                if blocking_deps:
                    skipped_blocked_eips[eip_name] = blocking_deps
                    continue

                spec = request.child_specs.get(eip_name)
                if spec is None:
                    spec = ChildEIPExecutionSpec(
                        eip_name=eip_name,
                        change_request=f"Deliver {eip_name} for {objective.title}",
                    )

                leaf_cost = (
                    spec.cost_usd
                    if spec.cost_usd is not None
                    else (child_ref.cost_usd or 0.05)
                )

                if (
                    objective.max_total_cost_usd is not None
                    and round(cumulative_cost_usd + leaf_cost, 9)
                    > round(objective.max_total_cost_usd, 9)
                ):
                    objective = self.hierarchy_service.record_leaf_outcome(
                        request.objective_id,
                        eip_name=eip_name,
                        verified=False,
                        evidence_fresh=False,
                        cost_usd=leaf_cost,
                    )
                    children_by_name = {child.eip_name: child for child in objective.child_eips}
                    continue

                if spec.simulate_unrecoverable_failure:
                    objective = self.hierarchy_service.record_leaf_outcome(
                        request.objective_id,
                        eip_name=eip_name,
                        verified=False,
                        evidence_fresh=False,
                        cost_usd=leaf_cost,
                    )
                    unrecoverable_failures.append(eip_name)
                    children_by_name = {child.eip_name: child for child in objective.child_eips}
                    continue

                effective_risk_tier = (
                    spec.risk_tier if spec.risk_tier is not None else child_ref.risk_tier
                )
                run_id = f"{request.run_id_prefix}-{eip_name}"
                vd_req = VerifiedDeliveryRequest(
                    run_id=run_id,
                    change_request=spec.change_request,
                    eip_name=eip_name,
                    risk_tier=effective_risk_tier,
                    domain_tags=list(spec.domain_tags),
                    builder_actor=spec.builder_actor,
                    builder_host_type=spec.builder_host_type,
                    verifier_actor=spec.verifier_actor,
                    human_approvers=list(spec.human_approvers),
                    explicit_authorization_ref=spec.explicit_authorization_ref,
                    commit_sha=spec.commit_sha,
                    stage=spec.stage,
                    cost_usd=leaf_cost,
                )
                leaf_res = self.leaf_workflow.execute(
                    vd_req,
                    simulate_drift_after_verify=spec.simulate_drift_after_verify,
                )
                cumulative_cost_usd = round(cumulative_cost_usd + leaf_cost, 9)
                child_results[eip_name] = leaf_res
                child_run_ids[eip_name] = run_id
                tranche_executed.append(eip_name)

                objective = self.hierarchy_service.record_leaf_outcome(
                    request.objective_id,
                    eip_name=eip_name,
                    verified=(
                        leaf_res.verification_passed
                        and leaf_res.release_decision.can_release
                    ),
                    evidence_fresh=leaf_res.freshness_passed,
                    evidence_digest=leaf_res.evidence_digest,
                    cost_usd=leaf_cost,
                )
                children_by_name = {child.eip_name: child for child in objective.child_eips}

            if tranche_executed:
                executed_tranches.append(tranche_executed)

        objective = self._load_engineering_objective(request.objective_id)
        all_leaves_passed = bool(objective.child_eips) and all(
            child.verified and child.evidence_fresh for child in objective.child_eips
        )

        if not (request.run_cross_eip_proof and all_leaves_passed):
            cross_passed = False
        elif callable(request.cross_eip_proof_callback):
            cross_passed = bool(request.cross_eip_proof_callback(objective, child_results))
        elif request.cross_eip_proof_callback is not None:
            cross_passed = bool(request.cross_eip_proof_callback)
        else:
            cross_passed = True

        objective = self.hierarchy_service.record_cross_eip_proof(
            request.objective_id,
            passed=cross_passed,
        )
        rollup_ok, rollup_reasons = evaluate_objective_rollup(objective)
        readiness = self.hierarchy_service.evaluate_release_readiness(
            request.objective_id,
            failed_unrecoverably=unrecoverable_failures,
        )

        return HierarchicalDeliveryResult(
            objective_id=request.objective_id,
            executed_tranches=executed_tranches,
            child_results=child_results,
            child_run_ids=child_run_ids,
            skipped_blocked_eips=skipped_blocked_eips,
            cross_eip_proof_passed=cross_passed,
            rollup_verified=rollup_ok,
            rollup_reasons=rollup_reasons,
            release_readiness=readiness,
            final_objective=objective,
        )
