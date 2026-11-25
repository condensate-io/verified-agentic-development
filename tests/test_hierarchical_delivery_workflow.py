"""Tests for HierarchicalDeliveryWorkflow 3-tier orchestration, tranches, and failure isolation."""

from __future__ import annotations

from pathlib import Path
import subprocess
from typing import Any

import pytest

from vad.contracts.intent_hierarchy import (
    ChildEIPRef,
    DecompositionStatus,
    EngineeringObjective,
    Programme,
    StrategicObjective,
)
from vad.contracts.models import AssuranceLevel, RiskTier
from vad.control_plane.intent_hierarchy_service import IntentHierarchyService
from vad.memory.gateway import MemoryGateway
from vad.memory.providers.null import NullMemoryProvider
from vad.policy.autonomy_matrix import PolicyStage
from vad.release.decision import ReleaseOutcome
from vad.server.db.store import ServerStore
from vad.signing.ed25519_local import LocalEd25519KeyProvider
from vad.workflows.hierarchical_delivery import (
    ChildEIPExecutionSpec,
    HierarchicalDeliveryRequest,
    HierarchicalDeliveryWorkflow,
)

_SEED_BYTES = bytes.fromhex(
    "9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60"
)


def _make_workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "workspace"
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "app.py").write_text(
        "def add(a: int, b: int) -> int:\n    return a + b\n",
        encoding="utf-8",
    )
    (ws / "test_app.py").write_text(
        "from app import add\n\ndef test_add() -> None:\n    assert add(2, 3) == 5\n",
        encoding="utf-8",
    )
    return ws


def _build_workflow(
    tmp_path: Path,
    *,
    db_name: str = "hierarchical.sqlite3",
    executed_commands: list[list[str]] | None = None,
) -> tuple[ServerStore, IntentHierarchyService, HierarchicalDeliveryWorkflow]:
    store = ServerStore(tmp_path / db_name)
    hierarchy_service = IntentHierarchyService(store=store)
    workspace = _make_workspace(tmp_path)
    key_provider = LocalEd25519KeyProvider.from_seed(
        _SEED_BYTES,
        key_id="ed25519-hierarchical-test",
        key_dir=tmp_path / ".vad" / "keys",
    )

    def _passing_executor(
        cmd_parts: list[str], **kwargs: Any
    ) -> subprocess.CompletedProcess[str]:
        if executed_commands is not None:
            executed_commands.append(list(cmd_parts))
        return subprocess.CompletedProcess(
            args=cmd_parts,
            returncode=0,
            stdout=f"ok: {' '.join(cmd_parts)}\n",
            stderr="",
        )

    workflow = HierarchicalDeliveryWorkflow(
        store=store,
        hierarchy_service=hierarchy_service,
        memory_gateway=MemoryGateway(provider=NullMemoryProvider()),
        key_provider=key_provider,
        workspace_dir=workspace,
        command_executor=_passing_executor,
    )
    return store, hierarchy_service, workflow


def _register_three_tier_objective(
    service: IntentHierarchyService,
    *,
    objective_id: str = "eo-delivery-dag",
    risk_floor: RiskTier = RiskTier.MEDIUM,
    assurance_floor: AssuranceLevel = AssuranceLevel.A2_INDEPENDENT,
    require_coordinated_release: bool = True,
    max_total_cost_usd: float | None = 1.0,
    eip_a_risk: RiskTier = RiskTier.MEDIUM,
    eip_b_risk: RiskTier = RiskTier.MEDIUM,
    eip_c_risk: RiskTier = RiskTier.HIGH,
    eip_a_cost: float = 0.04,
    eip_b_cost: float = 0.04,
    eip_c_cost: float = 0.06,
    approve: bool = True,
    submit_only: bool = False,
) -> EngineeringObjective:
    strategic = StrategicObjective(
        node_id=f"so-{objective_id}",
        title="Platform Reliability",
        description="Enforce verified hierarchical delivery across services.",
        risk_floor=RiskTier.LOW,
        assurance_floor=AssuranceLevel.A1_BASIC,
        inherited_invariants=["verifier_not_builder"],
        max_total_cost_usd=10.0,
    )
    programme = Programme(
        node_id=f"prog-{objective_id}",
        title="Core Delivery Programme",
        description="Coordinate multi-EIP verification tranches.",
        parent_id=strategic.node_id,
        risk_floor=risk_floor,
        assurance_floor=assurance_floor,
        inherited_invariants=["verifier_not_builder", "fresh_evidence_required"],
        max_total_cost_usd=5.0,
    )
    objective = EngineeringObjective(
        node_id=objective_id,
        title="Deliver Multi-EIP Auth Upgrade",
        description="Upgrade schema, telemetry, and integration enforcer.",
        parent_id=programme.node_id,
        risk_floor=risk_floor,
        assurance_floor=assurance_floor,
        inherited_invariants=[
            "verifier_not_builder",
            "fresh_evidence_required",
            "audit_trail_signed",
        ],
        max_total_cost_usd=max_total_cost_usd,
        require_coordinated_release=require_coordinated_release,
        child_eips=[
            ChildEIPRef(
                eip_name="eip-a",
                eip_digest="sha256:eip-a",
                risk_tier=eip_a_risk,
                assurance_level=AssuranceLevel.A2_INDEPENDENT,
                depends_on=[],
                cost_usd=eip_a_cost,
            ),
            ChildEIPRef(
                eip_name="eip-b",
                eip_digest="sha256:eip-b",
                risk_tier=eip_b_risk,
                assurance_level=AssuranceLevel.A2_INDEPENDENT,
                depends_on=[],
                cost_usd=eip_b_cost,
            ),
            ChildEIPRef(
                eip_name="eip-c",
                eip_digest="sha256:eip-c",
                risk_tier=eip_c_risk,
                assurance_level=AssuranceLevel.A3_ATTESTED,
                depends_on=["eip-a", "eip-b"],
                cost_usd=eip_c_cost,
            ),
        ],
    )
    service.register_strategic_objective(strategic)
    service.register_programme(programme)
    registered = service.register_engineering_objective(objective)

    if submit_only:
        return service.submit_for_approval(registered.node_id)
    if approve:
        service.submit_for_approval(registered.node_id)
        return service.approve_objective(
            registered.node_id,
            approver="guardian-lead",
            approver_role="release_guardian",
        )
    return registered


def _default_child_specs(
    *,
    eip_a_approvers: list[str] | None = None,
    eip_a_stage: PolicyStage = PolicyStage.CI_CD,
    eip_a_drift: bool = False,
    eip_a_unrecoverable: bool = False,
) -> dict[str, ChildEIPExecutionSpec]:
    return {
        "eip-a": ChildEIPExecutionSpec(
            eip_name="eip-a",
            change_request="Update auth schema validator in app module so that tokens validate.",
            domain_tags=["auth"],
            human_approvers=eip_a_approvers or [],
            stage=eip_a_stage,
            simulate_drift_after_verify=eip_a_drift,
            simulate_unrecoverable_failure=eip_a_unrecoverable,
        ),
        "eip-b": ChildEIPExecutionSpec(
            eip_name="eip-b",
            change_request="Emit telemetry counters in app module so that metrics are recorded.",
            domain_tags=["telemetry"],
        ),
        "eip-c": ChildEIPExecutionSpec(
            eip_name="eip-c",
            change_request="Wire auth enforcer in auth module so that guarded endpoints reject invalid tokens.",
            domain_tags=["auth", "security"],
            human_approvers=["guardian-alice"],
            stage=PolicyStage.DEPLOY,
        ),
    }


def test_full_three_tier_hierarchy_executes_tranches_and_verifies_rollup(
    tmp_path: Path,
) -> None:
    store, service, workflow = _build_workflow(tmp_path)
    _register_three_tier_objective(service, objective_id="eo-happy")

    callback_observations: list[tuple[str, list[str]]] = []

    def cross_eip_callback(obj: EngineeringObjective, results: dict[str, Any]) -> bool:
        callback_observations.append((obj.node_id, sorted(results.keys())))
        return True

    result = workflow.execute(
        HierarchicalDeliveryRequest(
            objective_id="eo-happy",
            run_id_prefix="happy-run",
            child_specs=_default_child_specs(),
            cross_eip_proof_callback=cross_eip_callback,
        )
    )

    assert result.objective_id == "eo-happy"
    assert result.executed_tranches == [["eip-a", "eip-b"], ["eip-c"]]
    assert result.skipped_blocked_eips == {}
    assert set(result.child_results.keys()) == {"eip-a", "eip-b", "eip-c"}
    assert result.child_run_ids == {
        "eip-a": "happy-run-eip-a",
        "eip-b": "happy-run-eip-b",
        "eip-c": "happy-run-eip-c",
    }
    for eip_name, child_res in result.child_results.items():
        assert child_res.verification_passed is True, eip_name
        assert child_res.freshness_passed is True, eip_name
        assert child_res.release_decision.can_release is True, eip_name
        assert child_res.audit_report.verified is True, eip_name

    assert callback_observations == [("eo-happy", ["eip-a", "eip-b", "eip-c"])]
    assert result.cross_eip_proof_passed is True
    assert result.rollup_verified is True
    assert result.rollup_reasons == []
    assert result.final_objective.decomposition_status == DecompositionStatus.VERIFIED
    assert result.release_readiness.rollup_verified is True
    assert result.release_readiness.can_release_objective is True
    assert result.release_readiness.releasable_child_eips == ["eip-a", "eip-b", "eip-c"]
    assert result.release_readiness.held_in_staging_eips == []
    assert result.release_readiness.rollback_required_eips == []

    persisted = store.load_intent_hierarchy_node("eo-happy")
    assert isinstance(persisted, EngineeringObjective)
    assert persisted.decomposition_status == DecompositionStatus.VERIFIED
    assert persisted.cross_eip_proof_passed is True


@pytest.mark.parametrize("submit_only", [False, True])
def test_unapproved_objective_raises_permission_error_before_any_leaf_executes(
    tmp_path: Path,
    submit_only: bool,
) -> None:
    executed_commands: list[list[str]] = []
    store, service, workflow = _build_workflow(
        tmp_path,
        db_name=f"unapproved-{submit_only}.sqlite3",
        executed_commands=executed_commands,
    )
    objective = _register_three_tier_objective(
        service,
        objective_id=f"eo-unapproved-{submit_only}",
        approve=False,
        submit_only=submit_only,
    )
    expected_status = (
        DecompositionStatus.AWAITING_APPROVAL
        if submit_only
        else DecompositionStatus.DRAFT
    )
    assert objective.decomposition_status == expected_status

    with pytest.raises(PermissionError):
        workflow.execute(
            HierarchicalDeliveryRequest(
                objective_id=objective.node_id,
                run_id_prefix="blocked-unapproved",
                child_specs=_default_child_specs(),
            )
        )

    assert executed_commands == []
    assert store.list_work_items(run_id="blocked-unapproved-eip-a") == []


def test_unrecoverable_failure_on_eip_a_blocks_eip_c_and_requires_rollback_when_coordinated(
    tmp_path: Path,
) -> None:
    _, service, workflow = _build_workflow(tmp_path)
    _register_three_tier_objective(
        service,
        objective_id="eo-unrecoverable",
        require_coordinated_release=True,
    )

    result = workflow.execute(
        HierarchicalDeliveryRequest(
            objective_id="eo-unrecoverable",
            run_id_prefix="unrec-run",
            child_specs=_default_child_specs(eip_a_unrecoverable=True),
        )
    )

    assert result.executed_tranches == [["eip-b"]]
    assert "eip-a" not in result.child_results
    assert "eip-b" in result.child_results
    assert result.child_results["eip-b"].release_decision.can_release is True
    assert result.skipped_blocked_eips == {"eip-c": ["eip-a"]}
    assert result.cross_eip_proof_passed is False
    assert result.rollup_verified is False
    assert result.release_readiness.can_release_objective is False
    assert result.release_readiness.rollback_required_eips == ["eip-b"]
    assert result.release_readiness.held_in_staging_eips == []
    assert result.release_readiness.releasable_child_eips == []


def test_missing_approval_on_eip_a_blocks_eip_c_and_holds_eip_b_in_staging(
    tmp_path: Path,
) -> None:
    _, service, workflow = _build_workflow(tmp_path)
    _register_three_tier_objective(
        service,
        objective_id="eo-recoverable-hold",
        require_coordinated_release=True,
        eip_a_risk=RiskTier.HIGH,
    )

    result = workflow.execute(
        HierarchicalDeliveryRequest(
            objective_id="eo-recoverable-hold",
            run_id_prefix="hold-run",
            child_specs=_default_child_specs(
                eip_a_approvers=[],
                eip_a_stage=PolicyStage.DEPLOY,
            ),
        )
    )

    assert result.executed_tranches == [["eip-a", "eip-b"]]
    assert (
        result.child_results["eip-a"].release_decision.outcome
        == ReleaseOutcome.HUMAN_APPROVAL_REQUIRED
    )
    assert result.child_results["eip-a"].release_decision.can_release is False
    assert result.child_results["eip-b"].release_decision.can_release is True
    assert result.skipped_blocked_eips == {"eip-c": ["eip-a"]}
    assert result.cross_eip_proof_passed is False
    assert result.rollup_verified is False
    assert result.release_readiness.can_release_objective is False
    assert result.release_readiness.held_in_staging_eips == ["eip-b"]
    assert result.release_readiness.rollback_required_eips == []


def test_drift_on_eip_a_blocks_eip_c_and_releases_eip_b_when_decoupled(
    tmp_path: Path,
) -> None:
    _, service, workflow = _build_workflow(tmp_path)
    _register_three_tier_objective(
        service,
        objective_id="eo-decoupled-drift",
        require_coordinated_release=False,
    )

    result = workflow.execute(
        HierarchicalDeliveryRequest(
            objective_id="eo-decoupled-drift",
            run_id_prefix="drift-run",
            child_specs=_default_child_specs(eip_a_drift=True),
        )
    )

    assert result.executed_tranches == [["eip-a", "eip-b"]]
    assert result.child_results["eip-a"].freshness_passed is False
    assert (
        result.child_results["eip-a"].release_decision.outcome
        == ReleaseOutcome.BLOCKED_STALE_EVIDENCE
    )
    assert result.child_results["eip-b"].release_decision.can_release is True
    assert result.skipped_blocked_eips == {"eip-c": ["eip-a"]}
    assert result.cross_eip_proof_passed is False
    assert result.rollup_verified is False
    assert result.release_readiness.can_release_objective is False
    assert result.release_readiness.releasable_child_eips == ["eip-b"]
    assert result.release_readiness.held_in_staging_eips == []
    assert result.release_readiness.rollback_required_eips == []


def test_budget_ceiling_exceeded_during_execution_halts_leaf_and_blocks_dependents(
    tmp_path: Path,
) -> None:
    _, service, workflow = _build_workflow(tmp_path)
    _register_three_tier_objective(
        service,
        objective_id="eo-budget-cap",
        require_coordinated_release=False,
        max_total_cost_usd=0.10,
        eip_a_cost=0.04,
        eip_b_cost=0.04,
        eip_c_cost=0.02,
    )

    specs = _default_child_specs()
    specs["eip-a"] = specs["eip-a"].model_copy(update={"cost_usd": 0.08})
    specs["eip-b"] = specs["eip-b"].model_copy(update={"cost_usd": 0.05})

    result = workflow.execute(
        HierarchicalDeliveryRequest(
            objective_id="eo-budget-cap",
            run_id_prefix="budget-run",
            child_specs=specs,
        )
    )

    assert result.executed_tranches == [["eip-a"]]
    assert "eip-a" in result.child_results
    assert "eip-b" not in result.child_results
    assert result.skipped_blocked_eips == {"eip-c": ["eip-b"]}
    assert result.rollup_verified is False
    assert result.release_readiness.can_release_objective is False
    assert result.release_readiness.releasable_child_eips == ["eip-a"]


def test_failed_cross_eip_proof_prevents_rollup_and_release(tmp_path: Path) -> None:
    _, service, workflow = _build_workflow(tmp_path)
    _register_three_tier_objective(
        service,
        objective_id="eo-cross-proof-fail",
        require_coordinated_release=True,
    )

    result = workflow.execute(
        HierarchicalDeliveryRequest(
            objective_id="eo-cross-proof-fail",
            run_id_prefix="crossfail-run",
            child_specs=_default_child_specs(),
            cross_eip_proof_callback=lambda obj, res: False,
        )
    )

    assert result.executed_tranches == [["eip-a", "eip-b"], ["eip-c"]]
    assert result.cross_eip_proof_passed is False
    assert result.rollup_verified is False
    assert "Cross-EIP integration proof obligations have not passed" in result.rollup_reasons
    assert result.final_objective.decomposition_status == DecompositionStatus.EXECUTING
    assert result.release_readiness.can_release_objective is False
    assert result.release_readiness.held_in_staging_eips == ["eip-a", "eip-b", "eip-c"]
