"""Unit tests for IntentHierarchyService governance, tranche scheduling, and release readiness."""

from __future__ import annotations

from typing import Any

import pytest

from vad.contracts.intent_hierarchy import (
    ChildEIPRef,
    DecompositionStatus,
    EngineeringObjective,
    IntentLevel,
    Programme,
    StrategicObjective,
)
from vad.contracts.models import AssuranceLevel, RiskTier
from vad.control_plane.intent_hierarchy_service import (
    IntentHierarchyService,
    ObjectiveReleaseReadiness,
    TrancheSchedulePlan,
)


class _MockHierarchyStore:
    """Lightweight duck-typed store implementing hierarchy CRUD hooks."""

    def __init__(self) -> None:
        self.nodes: dict[str, Any] = {}
        self.save_calls: list[str] = []
        self.load_calls: list[str] = []
        self.list_calls: list[IntentLevel | None] = []

    def save_intent_hierarchy_node(self, node: Any) -> None:
        self.save_calls.append(node.node_id)
        self.nodes[node.node_id] = node.model_copy(deep=True)

    def load_intent_hierarchy_node(self, node_id: str) -> Any | None:
        self.load_calls.append(node_id)
        node = self.nodes.get(node_id)
        return node.model_copy(deep=True) if node is not None else None

    def list_intent_hierarchy_nodes(
        self, *, level: IntentLevel | None = None
    ) -> list[Any]:
        self.list_calls.append(level)
        result = []
        for node in self.nodes.values():
            if level is None or node.level == level:
                result.append(node.model_copy(deep=True))
        return result


def _make_child(
    name: str,
    *,
    risk_tier: RiskTier = RiskTier.HIGH,
    assurance_level: AssuranceLevel = AssuranceLevel.A3_ATTESTED,
    depends_on: list[str] | None = None,
    verified: bool = False,
    evidence_fresh: bool = False,
    evidence_digest: str | None = None,
    cost_usd: float = 15.0,
) -> ChildEIPRef:
    return ChildEIPRef(
        eip_name=name,
        eip_digest=f"sha256:{name}",
        risk_tier=risk_tier,
        assurance_level=assurance_level,
        depends_on=depends_on or [],
        verified=verified,
        evidence_fresh=evidence_fresh,
        evidence_digest=evidence_digest,
        cost_usd=cost_usd,
    )


def _make_sample_nodes(
    *, require_coordinated_release: bool = True
) -> tuple[StrategicObjective, Programme, EngineeringObjective]:
    strategic = StrategicObjective(
        node_id="so-security",
        title="Platform Security",
        description="Enforce attested zero-trust controls across services.",
        risk_floor=RiskTier.MEDIUM,
        assurance_floor=AssuranceLevel.A2_INDEPENDENT,
        inherited_invariants=["verifier_not_builder", "no_plaintext_secrets"],
        max_total_cost_usd=300.0,
    )
    programme = Programme(
        node_id="prog-zero-trust",
        title="Zero-Trust Control Plane",
        description="Isolate worker credentials and enforce mTLS.",
        parent_id=strategic.node_id,
        risk_floor=RiskTier.HIGH,
        assurance_floor=AssuranceLevel.A3_ATTESTED,
        inherited_invariants=[
            "verifier_not_builder",
            "no_plaintext_secrets",
            "mtls_required",
        ],
        max_total_cost_usd=150.0,
    )
    engineering = EngineeringObjective(
        node_id="eo-worker-auth",
        title="Worker Credential Isolation",
        description="Roll out scoped host tokens and DSSE verification.",
        parent_id=programme.node_id,
        risk_floor=RiskTier.HIGH,
        assurance_floor=AssuranceLevel.A3_ATTESTED,
        inherited_invariants=[
            "verifier_not_builder",
            "no_plaintext_secrets",
            "mtls_required",
            "scoped_tokens",
        ],
        max_total_cost_usd=100.0,
        require_coordinated_release=require_coordinated_release,
        child_eips=[
            _make_child("eip-schema", cost_usd=20.0),
            _make_child("eip-telemetry", cost_usd=15.0),
            _make_child(
                "eip-enforcer",
                risk_tier=RiskTier.CRITICAL,
                assurance_level=AssuranceLevel.A4_RESTRICTED,
                depends_on=["eip-schema"],
                cost_usd=30.0,
            ),
            _make_child(
                "eip-rollout",
                depends_on=["eip-enforcer", "eip-telemetry"],
                cost_usd=25.0,
            ),
        ],
    )
    return strategic, programme, engineering


def test_three_tier_registration_and_chain_validation_errors() -> None:
    svc = IntentHierarchyService()
    strategic, programme, engineering = _make_sample_nodes()

    assert svc.register_strategic_objective(strategic) == strategic
    assert svc.register_programme(programme) == programme
    assert svc.register_engineering_objective(engineering) == engineering

    assert svc.get_node("so-security") == strategic
    assert svc.get_node("prog-zero-trust") == programme
    assert svc.get_node("eo-worker-auth") == engineering
    assert len(svc.list_nodes()) == 3
    assert [n.node_id for n in svc.list_nodes(level=IntentLevel.PROGRAMME)] == ["prog-zero-trust"]

    with pytest.raises(KeyError):
        svc.get_node("does-not-exist")

    # Programme floor downgrade, missing invariant, and budget breach
    with pytest.raises(ValueError, match="risk_floor"):
        svc.register_programme(
            programme.model_copy(
                update={"node_id": "prog-bad-risk", "risk_floor": RiskTier.LOW}
            )
        )
    with pytest.raises(ValueError, match="assurance_floor"):
        svc.register_programme(
            programme.model_copy(
                update={
                    "node_id": "prog-bad-assurance",
                    "assurance_floor": AssuranceLevel.A1_BASIC,
                }
            )
        )
    with pytest.raises(ValueError, match="no_plaintext_secrets"):
        svc.register_programme(
            programme.model_copy(
                update={
                    "node_id": "prog-bad-inv",
                    "inherited_invariants": ["verifier_not_builder"],
                }
            )
        )
    with pytest.raises(ValueError, match="max_total_cost_usd"):
        svc.register_programme(
            programme.model_copy(
                update={"node_id": "prog-over-budget", "max_total_cost_usd": 350.0}
            )
        )

    # EngineeringObjective floor downgrade, budget breach, and missing invariant against Programme
    with pytest.raises(ValueError, match="risk_floor"):
        svc.register_engineering_objective(
            engineering.model_copy(
                update={"node_id": "eo-bad-risk", "risk_floor": RiskTier.MEDIUM}
            )
        )
    with pytest.raises(ValueError, match="max_total_cost_usd"):
        svc.register_engineering_objective(
            engineering.model_copy(
                update={"node_id": "eo-over-budget", "max_total_cost_usd": 200.0}
            )
        )
    with pytest.raises(ValueError, match="mtls_required"):
        svc.register_engineering_objective(
            engineering.model_copy(
                update={
                    "node_id": "eo-bad-inv",
                    "inherited_invariants": ["verifier_not_builder", "no_plaintext_secrets"],
                }
            )
        )

    # Direct StrategicObjective -> EngineeringObjective registration works and enforces Strategic constraints
    direct_ok = engineering.model_copy(
        update={
            "node_id": "eo-direct",
            "parent_id": strategic.node_id,
            "inherited_invariants": ["verifier_not_builder", "no_plaintext_secrets"],
            "max_total_cost_usd": 250.0,
        }
    )
    assert svc.register_engineering_objective(direct_ok).node_id == "eo-direct"

    with pytest.raises(ValueError, match="max_total_cost_usd"):
        svc.register_engineering_objective(
            direct_ok.model_copy(
                update={"node_id": "eo-direct-over", "max_total_cost_usd": 350.0}
            )
        )


def test_pre_approval_dispatch_rejection_and_approval_gating() -> None:
    svc = IntentHierarchyService()
    strategic, programme, engineering = _make_sample_nodes()
    svc.register_strategic_objective(strategic)
    svc.register_programme(programme)
    svc.register_engineering_objective(engineering)

    # DRAFT rejected
    with pytest.raises(PermissionError):
        svc.plan_execution_tranches(engineering.node_id)

    # Submit -> AWAITING_APPROVAL still rejected
    submitted = svc.submit_for_approval(engineering.node_id)
    assert submitted.decomposition_status == DecompositionStatus.AWAITING_APPROVAL
    with pytest.raises(PermissionError):
        svc.plan_execution_tranches(engineering.node_id)

    # Unauthorized approver role rejected
    with pytest.raises(PermissionError):
        svc.approve_objective(
            engineering.node_id,
            approver="bob.builder",
            approver_role="builder",
        )

    # Hierarchy re-validation catches parent floor tightening before approval
    tighter_prog = programme.model_copy(update={"risk_floor": RiskTier.CRITICAL})
    svc.register_programme(tighter_prog)
    with pytest.raises(ValueError, match="risk_floor"):
        svc.approve_objective(
            engineering.node_id,
            approver="alice.guardian",
            approver_role="release_guardian",
        )

    # Restore valid parent and approve
    svc.register_programme(programme)
    approved = svc.approve_objective(
        engineering.node_id,
        approver="alice.guardian",
        approver_role="release_guardian",
    )
    assert approved.decomposition_status == DecompositionStatus.APPROVED
    assert approved.approved_by == "alice.guardian"

    plan = svc.plan_execution_tranches(engineering.node_id)
    assert isinstance(plan, TrancheSchedulePlan)
    assert plan.tranches == [
        ["eip-schema", "eip-telemetry"],
        ["eip-enforcer"],
        ["eip-rollout"],
    ]

    # FAILED and CANCELLED cannot plan tranches
    svc.register_engineering_objective(
        approved.model_copy(
            update={"node_id": "eo-failed", "decomposition_status": DecompositionStatus.FAILED}
        )
    )
    svc.register_engineering_objective(
        approved.model_copy(
            update={
                "node_id": "eo-cancelled",
                "decomposition_status": DecompositionStatus.CANCELLED,
            }
        )
    )
    with pytest.raises(PermissionError):
        svc.plan_execution_tranches("eo-failed")
    with pytest.raises(PermissionError):
        svc.plan_execution_tranches("eo-cancelled")


def test_topological_tranche_progression_and_downstream_blocking() -> None:
    svc = IntentHierarchyService()
    strategic, programme, engineering = _make_sample_nodes()
    svc.register_strategic_objective(strategic)
    svc.register_programme(programme)
    svc.register_engineering_objective(engineering)
    svc.submit_for_approval(engineering.node_id)
    svc.approve_objective(engineering.node_id, approver="guardian-1")

    # Initial state: tranche 1 ready, tranche 2 and 3 blocked
    plan0 = svc.plan_execution_tranches(engineering.node_id)
    assert plan0.ready_to_dispatch == ["eip-schema", "eip-telemetry"]
    assert plan0.completed_eips == []
    assert plan0.blocked_dependents == {
        "eip-enforcer": ["eip-schema"],
        "eip-rollout": ["eip-schema", "eip-telemetry", "eip-enforcer"],
    }
    assert plan0.total_cost_usd == pytest.approx(90.0)
    assert plan0.remaining_budget_usd == pytest.approx(10.0)

    # Complete eip-schema -> eip-enforcer becomes ready alongside eip-telemetry
    obj1 = svc.record_leaf_outcome(
        engineering.node_id,
        eip_name="eip-schema",
        verified=True,
        evidence_fresh=True,
        evidence_digest="sha256:ev-schema",
        cost_usd=22.0,
    )
    assert obj1.decomposition_status == DecompositionStatus.EXECUTING
    plan1 = svc.plan_execution_tranches(engineering.node_id)
    assert plan1.completed_eips == ["eip-schema"]
    assert plan1.ready_to_dispatch == ["eip-telemetry", "eip-enforcer"]
    assert plan1.blocked_dependents == {
        "eip-rollout": ["eip-telemetry", "eip-enforcer"],
    }
    assert plan1.total_cost_usd == pytest.approx(92.0)
    assert plan1.remaining_budget_usd == pytest.approx(8.0)

    # Complete eip-enforcer (tranche 2) while eip-telemetry is still unverified
    svc.record_leaf_outcome(
        engineering.node_id,
        eip_name="eip-enforcer",
        verified=True,
        evidence_fresh=True,
        evidence_digest="sha256:ev-enforcer",
    )
    plan2 = svc.plan_execution_tranches(engineering.node_id)
    assert plan2.completed_eips == ["eip-schema", "eip-enforcer"]
    assert plan2.ready_to_dispatch == ["eip-telemetry"]
    assert plan2.blocked_dependents == {"eip-rollout": ["eip-telemetry"]}

    # If eip-schema loses freshness, eip-enforcer and eip-rollout become blocked again
    svc.record_leaf_outcome(
        engineering.node_id,
        eip_name="eip-schema",
        verified=True,
        evidence_fresh=False,
    )
    plan_stale = svc.plan_execution_tranches(engineering.node_id)
    assert plan_stale.ready_to_dispatch == ["eip-schema", "eip-telemetry"]
    assert plan_stale.blocked_dependents == {
        "eip-enforcer": ["eip-schema"],
        "eip-rollout": ["eip-schema", "eip-telemetry"],
    }

    # Re-verify eip-schema, complete eip-telemetry -> eip-rollout becomes ready
    svc.record_leaf_outcome(
        engineering.node_id,
        eip_name="eip-schema",
        verified=True,
        evidence_fresh=True,
    )
    svc.record_leaf_outcome(
        engineering.node_id,
        eip_name="eip-telemetry",
        verified=True,
        evidence_fresh=True,
    )
    plan3 = svc.plan_execution_tranches(engineering.node_id)
    assert plan3.ready_to_dispatch == ["eip-rollout"]
    assert plan3.blocked_dependents == {}

    # Complete eip-rollout, then pass cross-EIP proof -> VERIFIED
    obj_leaves_done = svc.record_leaf_outcome(
        engineering.node_id,
        eip_name="eip-rollout",
        verified=True,
        evidence_fresh=True,
    )
    assert obj_leaves_done.decomposition_status == DecompositionStatus.EXECUTING

    verified_obj = svc.record_cross_eip_proof(engineering.node_id, passed=True)
    assert verified_obj.decomposition_status == DecompositionStatus.VERIFIED
    plan_done = svc.plan_execution_tranches(engineering.node_id)
    assert plan_done.ready_to_dispatch == []
    assert plan_done.completed_eips == [
        "eip-schema",
        "eip-telemetry",
        "eip-enforcer",
        "eip-rollout",
    ]


def test_coordinated_release_vs_decoupled_tranche_release() -> None:
    # 1) Coordinated release
    svc_coord = IntentHierarchyService()
    strategic, programme, coord_eng = _make_sample_nodes(require_coordinated_release=True)
    svc_coord.register_strategic_objective(strategic)
    svc_coord.register_programme(programme)
    svc_coord.register_engineering_objective(coord_eng)
    svc_coord.submit_for_approval(coord_eng.node_id)
    svc_coord.approve_objective(coord_eng.node_id, approver="guardian-1")

    svc_coord.record_leaf_outcome(
        coord_eng.node_id,
        eip_name="eip-schema",
        verified=True,
        evidence_fresh=True,
    )
    svc_coord.record_leaf_outcome(
        coord_eng.node_id,
        eip_name="eip-telemetry",
        verified=True,
        evidence_fresh=True,
    )

    # Partial completion -> verified children held in staging, nothing releasable
    staging_readiness = svc_coord.evaluate_release_readiness(coord_eng.node_id)
    assert isinstance(staging_readiness, ObjectiveReleaseReadiness)
    assert staging_readiness.rollup_verified is False
    assert staging_readiness.can_release_objective is False
    assert staging_readiness.releasable_child_eips == []
    assert staging_readiness.held_in_staging_eips == ["eip-schema", "eip-telemetry"]
    assert staging_readiness.rollback_required_eips == []

    # Unrecoverable failure -> already verified children require rollback
    rollback_readiness = svc_coord.evaluate_release_readiness(
        coord_eng.node_id,
        failed_unrecoverably=["eip-enforcer"],
    )
    assert rollback_readiness.can_release_objective is False
    assert rollback_readiness.releasable_child_eips == []
    assert rollback_readiness.held_in_staging_eips == []
    assert rollback_readiness.rollback_required_eips == ["eip-schema", "eip-telemetry"]

    # Complete remaining leaves + cross-EIP proof -> objective releasable
    svc_coord.record_leaf_outcome(
        coord_eng.node_id,
        eip_name="eip-enforcer",
        verified=True,
        evidence_fresh=True,
    )
    svc_coord.record_leaf_outcome(
        coord_eng.node_id,
        eip_name="eip-rollout",
        verified=True,
        evidence_fresh=True,
    )
    svc_coord.record_cross_eip_proof(coord_eng.node_id, passed=True)
    full_readiness = svc_coord.evaluate_release_readiness(coord_eng.node_id)
    assert full_readiness.rollup_verified is True
    assert full_readiness.can_release_objective is True
    assert full_readiness.releasable_child_eips == [
        "eip-schema",
        "eip-telemetry",
        "eip-enforcer",
        "eip-rollout",
    ]
    assert full_readiness.held_in_staging_eips == []
    assert full_readiness.rollback_required_eips == []

    # 2) Decoupled tranche release
    svc_decoupled = IntentHierarchyService()
    decoupled_eng = coord_eng.model_copy(
        update={
            "node_id": "eo-decoupled",
            "require_coordinated_release": False,
        }
    )
    svc_decoupled.register_strategic_objective(strategic)
    svc_decoupled.register_programme(programme)
    svc_decoupled.register_engineering_objective(decoupled_eng)
    svc_decoupled.submit_for_approval(decoupled_eng.node_id)
    svc_decoupled.approve_objective(decoupled_eng.node_id, approver="guardian-1")

    svc_decoupled.record_leaf_outcome(
        decoupled_eng.node_id,
        eip_name="eip-schema",
        verified=True,
        evidence_fresh=True,
    )
    svc_decoupled.record_leaf_outcome(
        decoupled_eng.node_id,
        eip_name="eip-enforcer",
        verified=True,
        evidence_fresh=True,
    )

    partial_decoupled = svc_decoupled.evaluate_release_readiness(decoupled_eng.node_id)
    assert partial_decoupled.rollup_verified is False
    assert partial_decoupled.can_release_objective is False
    assert partial_decoupled.releasable_child_eips == ["eip-schema", "eip-enforcer"]
    assert partial_decoupled.held_in_staging_eips == []

    # If eip-schema becomes stale, eip-enforcer becomes blocked and is held from decoupled release
    svc_decoupled.record_leaf_outcome(
        decoupled_eng.node_id,
        eip_name="eip-schema",
        verified=True,
        evidence_fresh=False,
    )
    blocked_decoupled = svc_decoupled.evaluate_release_readiness(decoupled_eng.node_id)
    assert blocked_decoupled.releasable_child_eips == []
    assert blocked_decoupled.held_in_staging_eips == ["eip-schema", "eip-enforcer"]

    # Complete all leaves + proof -> full objective release allowed
    svc_decoupled.record_leaf_outcome(
        decoupled_eng.node_id,
        eip_name="eip-schema",
        verified=True,
        evidence_fresh=True,
    )
    svc_decoupled.record_leaf_outcome(
        decoupled_eng.node_id,
        eip_name="eip-telemetry",
        verified=True,
        evidence_fresh=True,
    )
    svc_decoupled.record_leaf_outcome(
        decoupled_eng.node_id,
        eip_name="eip-rollout",
        verified=True,
        evidence_fresh=True,
    )
    svc_decoupled.record_cross_eip_proof(decoupled_eng.node_id, passed=True)
    complete_decoupled = svc_decoupled.evaluate_release_readiness(decoupled_eng.node_id)
    assert complete_decoupled.rollup_verified is True
    assert complete_decoupled.can_release_objective is True
    assert complete_decoupled.releasable_child_eips == [
        "eip-schema",
        "eip-telemetry",
        "eip-enforcer",
        "eip-rollout",
    ]


def test_duck_typed_store_persistence_across_service_instances() -> None:
    store = _MockHierarchyStore()
    svc_writer = IntentHierarchyService(store=store)
    strategic, programme, engineering = _make_sample_nodes(require_coordinated_release=True)

    svc_writer.register_strategic_objective(strategic)
    svc_writer.register_programme(programme)
    svc_writer.register_engineering_objective(engineering)
    svc_writer.submit_for_approval(engineering.node_id)
    svc_writer.approve_objective(engineering.node_id, approver="guardian-store")
    svc_writer.record_leaf_outcome(
        engineering.node_id,
        eip_name="eip-schema",
        verified=True,
        evidence_fresh=True,
        evidence_digest="sha256:stored-schema",
    )

    assert "so-security" in store.save_calls
    assert "prog-zero-trust" in store.save_calls
    assert "eo-worker-auth" in store.save_calls

    # Fresh service instance with empty in-memory maps reads everything from store
    svc_reader = IntentHierarchyService(store=store)
    loaded_eng = svc_reader.get_node(engineering.node_id)
    assert isinstance(loaded_eng, EngineeringObjective)
    assert loaded_eng.decomposition_status == DecompositionStatus.EXECUTING
    assert loaded_eng.approved_by == "guardian-store"
    assert loaded_eng.child_eips[0].verified is True
    assert engineering.node_id in store.load_calls

    all_nodes = svc_reader.list_nodes()
    assert {n.node_id for n in all_nodes} == {
        "so-security",
        "prog-zero-trust",
        "eo-worker-auth",
    }
    eng_only = svc_reader.list_nodes(level=IntentLevel.ENGINEERING_OBJECTIVE)
    assert [n.node_id for n in eng_only] == ["eo-worker-auth"]
    assert None in store.list_calls
    assert IntentLevel.ENGINEERING_OBJECTIVE in store.list_calls

    plan = svc_reader.plan_execution_tranches(engineering.node_id)
    assert plan.completed_eips == ["eip-schema"]
    assert plan.ready_to_dispatch == ["eip-telemetry", "eip-enforcer"]
