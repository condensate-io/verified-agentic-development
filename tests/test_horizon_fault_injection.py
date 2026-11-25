"""Deterministic Horizon fault-injection & fail-closed security tests."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
from pathlib import Path
from typing import Any

import pytest

from vad.adapters.domain_pack import DomainKind
from vad.audit.verify_trail import verify_objective_audit_trail, verify_run_audit_trail
from vad.contracts.intent_hierarchy import (
    ChildEIPRef,
    DecompositionStatus,
    EngineeringObjective,
    Programme,
    StrategicObjective,
)
from vad.contracts.models import AssuranceLevel, RiskTier
from vad.control_plane.intent_hierarchy_service import IntentHierarchyService
from vad.control_plane.remote_worker_coordinator import RemoteWorkerCoordinator
from vad.control_plane.remote_workers import (
    ArtifactTransferManifest,
    RemoteDispatchEnvelope,
    RemoteExecutionReceipt,
    RemoteWorkerAdmissionController,
    RemoteWorkerIdentity,
    RemoteWorkerPoolPolicy,
    WorkerAuthMethod,
    WorkerExecutionMode,
)
from vad.control_plane.sessions import HostRole, HostSessionManager
from vad.memory.gateway import MemoryGateway
from vad.memory.providers.null import NullMemoryProvider
from vad.policy.autonomy_matrix import PolicyStage
from vad.release.decision import ReleaseOutcome
from vad.server.db.store import ServerStore
from vad.signing.ed25519_local import LocalEd25519KeyProvider
from vad.workflows.domain_pack_execution import (
    DomainExecutionRequest,
    DomainPackExecutionRunner,
)
from vad.workflows.hierarchical_delivery import (
    ChildEIPExecutionSpec,
    HierarchicalDeliveryRequest,
    HierarchicalDeliveryWorkflow,
)
from vad.workflows.verified_delivery import (
    VerifiedDeliveryRequest,
    VerifiedDeliveryWorkflow,
)

_SEED_BYTES = bytes.fromhex(
    "9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60"
)


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


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


def _build_hierarchical_workflow(
    tmp_path: Path,
    *,
    db_name: str = "horizon_fault.sqlite3",
    executed_commands: list[list[str]] | None = None,
) -> tuple[
    Path,
    ServerStore,
    IntentHierarchyService,
    HierarchicalDeliveryWorkflow,
    LocalEd25519KeyProvider,
]:
    db_path = tmp_path / db_name
    store = ServerStore(db_path)
    hierarchy_service = IntentHierarchyService(store=store)
    workspace = _make_workspace(tmp_path)
    key_provider = LocalEd25519KeyProvider.from_seed(
        _SEED_BYTES,
        key_id="ed25519-horizon-fault",
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
    return db_path, store, hierarchy_service, workflow, key_provider


def _register_three_tier_dag(
    service: IntentHierarchyService,
    *,
    objective_id: str,
    risk_floor: RiskTier = RiskTier.MEDIUM,
    assurance_floor: AssuranceLevel = AssuranceLevel.A2_INDEPENDENT,
    require_coordinated_release: bool = True,
    max_total_cost_usd: float | None = 1.0,
    child_eips: list[ChildEIPRef] | None = None,
    approve: bool = True,
) -> tuple[StrategicObjective, Programme, EngineeringObjective]:
    strategic = StrategicObjective(
        node_id=f"so-{objective_id}",
        title="Platform Trust & Governance",
        description="Enforce non-downgradable floors and verified rollup.",
        risk_floor=RiskTier.LOW,
        assurance_floor=AssuranceLevel.A1_BASIC,
        inherited_invariants=["verifier_not_builder", "no_plaintext_secrets"],
        max_total_cost_usd=20.0,
    )
    programme = Programme(
        node_id=f"prog-{objective_id}",
        title="Horizon Verification Programme",
        description="Coordinate multi-EIP verification tranches.",
        parent_id=strategic.node_id,
        risk_floor=risk_floor,
        assurance_floor=assurance_floor,
        inherited_invariants=[
            "verifier_not_builder",
            "no_plaintext_secrets",
            "fresh_evidence_required",
        ],
        max_total_cost_usd=10.0,
    )
    default_children = [
        ChildEIPRef(
            eip_name="eip-a",
            eip_digest="a" * 64,
            risk_tier=risk_floor,
            assurance_level=assurance_floor,
            depends_on=[],
            cost_usd=0.04,
        ),
        ChildEIPRef(
            eip_name="eip-b",
            eip_digest="b" * 64,
            risk_tier=risk_floor,
            assurance_level=assurance_floor,
            depends_on=[],
            cost_usd=0.04,
        ),
        ChildEIPRef(
            eip_name="eip-c",
            eip_digest="c" * 64,
            risk_tier=RiskTier.HIGH,
            assurance_level=AssuranceLevel.A3_ATTESTED,
            depends_on=["eip-a", "eip-b"],
            cost_usd=0.06,
        ),
    ]
    objective = EngineeringObjective(
        node_id=objective_id,
        title="Deliver Governed Multi-EIP Objective",
        description="Execute leaf EIPs across topological tranches.",
        parent_id=programme.node_id,
        risk_floor=risk_floor,
        assurance_floor=assurance_floor,
        inherited_invariants=[
            "verifier_not_builder",
            "no_plaintext_secrets",
            "fresh_evidence_required",
        ],
        max_total_cost_usd=max_total_cost_usd,
        require_coordinated_release=require_coordinated_release,
        child_eips=child_eips if child_eips is not None else default_children,
    )

    service.register_strategic_objective(strategic)
    service.register_programme(programme)
    registered = service.register_engineering_objective(objective)
    if approve:
        service.submit_for_approval(registered.node_id)
        registered = service.approve_objective(
            registered.node_id,
            approver="guardian-lead",
            approver_role="release_guardian",
        )
    return strategic, programme, registered


def _make_remote_worker(
    *,
    worker_id: str = "remote-verifier-01",
    host_type: str = "verifier_runner",
    role: HostRole = HostRole.VERIFIER,
    auth_method: WorkerAuthMethod = WorkerAuthMethod.MTLS,
    identity_subject: str = "spiffe://vad.local/ns/workers/sa/remote-verifier-01",
    endpoint_url: str = "https://127.0.0.1:8443/verifier",
    cert_or_issuer_ref: str = "vault://pki/vad/remote-verifier-01",
    allowed_risk_tiers: list[RiskTier] | None = None,
) -> RemoteWorkerIdentity:
    return RemoteWorkerIdentity(
        worker_id=worker_id,
        host_type=host_type,
        role=role,
        auth_method=auth_method,
        identity_subject=identity_subject,
        endpoint_url=endpoint_url,
        cert_or_issuer_ref=cert_or_issuer_ref,
        allowed_risk_tiers=allowed_risk_tiers
        or [RiskTier.LOW, RiskTier.MEDIUM, RiskTier.HIGH],
    )


def _make_manifest_and_payloads(
    *,
    transfer_id: str,
    run_id: str,
    work_item_id: str,
    fence_token: int,
    payloads: dict[str, bytes],
) -> tuple[ArtifactTransferManifest, dict[str, bytes]]:
    manifest = ArtifactTransferManifest(
        transfer_id=transfer_id,
        run_id=run_id,
        work_item_id=work_item_id,
        fence_token=fence_token,
        artifacts={path: _sha256_hex(content) for path, content in payloads.items()},
    )
    return manifest, payloads


def test_fault_1_unapproved_objective_decomposition_blocks_dispatch(
    tmp_path: Path,
) -> None:
    executed_commands: list[list[str]] = []
    _, store, service, workflow, _ = _build_hierarchical_workflow(
        tmp_path,
        db_name="fault1.sqlite3",
        executed_commands=executed_commands,
    )
    _, _, objective = _register_three_tier_dag(
        service,
        objective_id="eo-fault-1",
        approve=False,
    )
    assert objective.decomposition_status == DecompositionStatus.DRAFT

    with pytest.raises(PermissionError):
        service.plan_execution_tranches(objective.node_id)

    with pytest.raises(PermissionError):
        workflow.execute(
            HierarchicalDeliveryRequest(
                objective_id=objective.node_id,
                run_id_prefix="fault1-draft",
            )
        )
    assert executed_commands == []
    assert store.list_work_items(run_id="fault1-draft-eip-a") == []

    awaiting = service.submit_for_approval(objective.node_id)
    assert awaiting.decomposition_status == DecompositionStatus.AWAITING_APPROVAL

    with pytest.raises(PermissionError):
        service.plan_execution_tranches(objective.node_id)

    with pytest.raises(PermissionError):
        workflow.execute(
            HierarchicalDeliveryRequest(
                objective_id=objective.node_id,
                run_id_prefix="fault1-awaiting",
            )
        )
    assert executed_commands == []

    with pytest.raises(PermissionError):
        service.approve_objective(
            objective.node_id,
            approver="codex-builder-1",
            approver_role="builder",
        )

    persisted = store.load_intent_hierarchy_node(objective.node_id)
    assert isinstance(persisted, EngineeringObjective)
    assert persisted.decomposition_status == DecompositionStatus.AWAITING_APPROVAL
    assert persisted.approved_by is None


def test_fault_2_hierarchy_floor_and_invariant_downgrade_rejected(
    tmp_path: Path,
) -> None:
    db_path, store, service, workflow, key_provider = _build_hierarchical_workflow(
        tmp_path,
        db_name="fault2.sqlite3",
    )

    strategic = StrategicObjective(
        node_id="so-fault-2",
        title="Strict Assurance Mandate",
        description="Enforce HIGH risk floor, A3 assurance floor, and invariants.",
        risk_floor=RiskTier.HIGH,
        assurance_floor=AssuranceLevel.A3_ATTESTED,
        inherited_invariants=["verifier_not_builder", "no_plaintext_secrets"],
        max_total_cost_usd=50.0,
    )
    service.register_strategic_objective(strategic)

    # Programme downgrading risk_floor, assurance_floor, or dropping an invariant fails closed.
    with pytest.raises(ValueError, match="risk_floor"):
        service.register_programme(
            Programme(
                node_id="prog-bad-risk",
                title="Downgraded Risk Programme",
                description="Attempts to lower risk_floor.",
                parent_id=strategic.node_id,
                risk_floor=RiskTier.MEDIUM,
                assurance_floor=AssuranceLevel.A3_ATTESTED,
                inherited_invariants=["verifier_not_builder", "no_plaintext_secrets"],
                max_total_cost_usd=25.0,
            )
        )

    with pytest.raises(ValueError, match="assurance_floor"):
        service.register_programme(
            Programme(
                node_id="prog-bad-assurance",
                title="Downgraded Assurance Programme",
                description="Attempts to lower assurance_floor.",
                parent_id=strategic.node_id,
                risk_floor=RiskTier.HIGH,
                assurance_floor=AssuranceLevel.A2_INDEPENDENT,
                inherited_invariants=["verifier_not_builder", "no_plaintext_secrets"],
                max_total_cost_usd=25.0,
            )
        )

    with pytest.raises(ValueError, match="dropped inherited invariants"):
        service.register_programme(
            Programme(
                node_id="prog-bad-invariant",
                title="Dropped Invariant Programme",
                description="Attempts to drop no_plaintext_secrets.",
                parent_id=strategic.node_id,
                risk_floor=RiskTier.HIGH,
                assurance_floor=AssuranceLevel.A3_ATTESTED,
                inherited_invariants=["verifier_not_builder"],
                max_total_cost_usd=25.0,
            )
        )

    programme = Programme(
        node_id="prog-fault-2",
        title="Valid Strict Programme",
        description="Preserves strategic floors and invariants.",
        parent_id=strategic.node_id,
        risk_floor=RiskTier.HIGH,
        assurance_floor=AssuranceLevel.A3_ATTESTED,
        inherited_invariants=["verifier_not_builder", "no_plaintext_secrets"],
        max_total_cost_usd=25.0,
    )
    service.register_programme(programme)

    valid_child = ChildEIPRef(
        eip_name="eip-strict-leaf",
        eip_digest="a" * 64,
        risk_tier=RiskTier.HIGH,
        assurance_level=AssuranceLevel.A3_ATTESTED,
        cost_usd=1.0,
    )

    # EngineeringObjective downgrading floor or dropping invariant fails closed.
    with pytest.raises(ValueError, match="risk_floor"):
        service.register_engineering_objective(
            EngineeringObjective(
                node_id="eo-bad-risk",
                title="Bad EO Risk",
                description="Downgrades risk_floor.",
                parent_id=programme.node_id,
                risk_floor=RiskTier.MEDIUM,
                assurance_floor=AssuranceLevel.A3_ATTESTED,
                inherited_invariants=["verifier_not_builder", "no_plaintext_secrets"],
                max_total_cost_usd=10.0,
                child_eips=[valid_child],
            )
        )

    with pytest.raises(ValueError, match="assurance_floor"):
        service.register_engineering_objective(
            EngineeringObjective(
                node_id="eo-bad-assurance",
                title="Bad EO Assurance",
                description="Downgrades assurance_floor.",
                parent_id=programme.node_id,
                risk_floor=RiskTier.HIGH,
                assurance_floor=AssuranceLevel.A2_INDEPENDENT,
                inherited_invariants=["verifier_not_builder", "no_plaintext_secrets"],
                max_total_cost_usd=10.0,
                child_eips=[valid_child],
            )
        )

    with pytest.raises(ValueError, match="dropped inherited invariants"):
        service.register_engineering_objective(
            EngineeringObjective(
                node_id="eo-bad-invariant",
                title="Bad EO Invariant",
                description="Drops inherited invariant.",
                parent_id=programme.node_id,
                risk_floor=RiskTier.HIGH,
                assurance_floor=AssuranceLevel.A3_ATTESTED,
                inherited_invariants=["verifier_not_builder"],
                max_total_cost_usd=10.0,
                child_eips=[valid_child],
            )
        )

    # ChildEIPRef below objective's risk_floor or assurance_floor fails closed.
    with pytest.raises(ValueError, match="risk_tier"):
        service.register_engineering_objective(
            EngineeringObjective(
                node_id="eo-bad-child-risk",
                title="Bad Child Risk",
                description="Child risk below floor.",
                parent_id=programme.node_id,
                risk_floor=RiskTier.HIGH,
                assurance_floor=AssuranceLevel.A3_ATTESTED,
                inherited_invariants=["verifier_not_builder", "no_plaintext_secrets"],
                max_total_cost_usd=10.0,
                child_eips=[
                    ChildEIPRef(
                        eip_name="eip-weak-risk",
                        eip_digest="b" * 64,
                        risk_tier=RiskTier.MEDIUM,
                        assurance_level=AssuranceLevel.A3_ATTESTED,
                        cost_usd=1.0,
                    )
                ],
            )
        )

    with pytest.raises(ValueError, match="assurance_level"):
        service.register_engineering_objective(
            EngineeringObjective(
                node_id="eo-bad-child-assurance",
                title="Bad Child Assurance",
                description="Child assurance below floor.",
                parent_id=programme.node_id,
                risk_floor=RiskTier.HIGH,
                assurance_floor=AssuranceLevel.A3_ATTESTED,
                inherited_invariants=["verifier_not_builder", "no_plaintext_secrets"],
                max_total_cost_usd=10.0,
                child_eips=[
                    ChildEIPRef(
                        eip_name="eip-weak-assurance",
                        eip_digest="c" * 64,
                        risk_tier=RiskTier.HIGH,
                        assurance_level=AssuranceLevel.A2_INDEPENDENT,
                        cost_usd=1.0,
                    )
                ],
            )
        )

    # Register and execute a valid strict objective, then tamper directly in SQLite.
    valid_obj = EngineeringObjective(
        node_id="eo-fault-2-valid",
        title="Valid Strict EO",
        description="Executes cleanly before direct SQLite tamper.",
        parent_id=programme.node_id,
        risk_floor=RiskTier.HIGH,
        assurance_floor=AssuranceLevel.A3_ATTESTED,
        inherited_invariants=["verifier_not_builder", "no_plaintext_secrets"],
        max_total_cost_usd=10.0,
        child_eips=[valid_child],
    )
    service.register_engineering_objective(valid_obj)
    service.submit_for_approval(valid_obj.node_id)
    service.approve_objective(
        valid_obj.node_id,
        approver="guardian-alice",
        approver_role="release_guardian",
    )
    exec_result = workflow.execute(
        HierarchicalDeliveryRequest(
            objective_id=valid_obj.node_id,
            run_id_prefix="fault2-run",
            child_specs={
                "eip-strict-leaf": ChildEIPExecutionSpec(
                    eip_name="eip-strict-leaf",
                    change_request="Update auth guard in app module.",
                    human_approvers=["guardian-alice"],
                    stage=PolicyStage.DEPLOY,
                    cost_usd=1.0,
                )
            },
        )
    )
    assert exec_result.rollup_verified is True

    baseline_report = verify_objective_audit_trail(
        store,
        valid_obj.node_id,
        run_id_prefix="fault2-run",
        key_provider=key_provider,
    )
    assert baseline_report.verified is True

    # Tamper EngineeringObjective directly in SQLite (downgrade floor + drop invariant).
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT payload_json FROM intent_hierarchy_nodes WHERE node_id = ?",
            (valid_obj.node_id,),
        ).fetchone()
        assert row is not None
        tampered_payload = json.loads(row[0])
        tampered_payload["risk_floor"] = RiskTier.LOW.value
        tampered_payload["inherited_invariants"] = ["verifier_not_builder"]
        conn.execute(
            "UPDATE intent_hierarchy_nodes SET risk_floor = ?, payload_json = ? WHERE node_id = ?",
            (
                RiskTier.LOW.value,
                json.dumps(tampered_payload, sort_keys=True),
                valid_obj.node_id,
            ),
        )

    tampered_report = verify_objective_audit_trail(
        store,
        valid_obj.node_id,
        run_id_prefix="fault2-run",
        key_provider=key_provider,
    )
    assert tampered_report.verified is False
    checks = {item.check: item.passed for item in tampered_report.checks}
    assert checks["hierarchy_chain_floors"] is False
    assert any("hierarchy_chain_floors" in failure for failure in tampered_report.failures)


def test_fault_3_aggregate_budget_ceiling_overrun_halts_programme_and_blocks_rollup(
    tmp_path: Path,
) -> None:
    _, _, service, workflow, _ = _build_hierarchical_workflow(
        tmp_path,
        db_name="fault3.sqlite3",
    )
    children = [
        ChildEIPRef(
            eip_name="eip-a",
            eip_digest="a" * 64,
            risk_tier=RiskTier.MEDIUM,
            assurance_level=AssuranceLevel.A2_INDEPENDENT,
            depends_on=[],
            cost_usd=0.04,
        ),
        ChildEIPRef(
            eip_name="eip-b",
            eip_digest="b" * 64,
            risk_tier=RiskTier.MEDIUM,
            assurance_level=AssuranceLevel.A2_INDEPENDENT,
            depends_on=[],
            cost_usd=0.04,
        ),
        ChildEIPRef(
            eip_name="eip-c",
            eip_digest="c" * 64,
            risk_tier=RiskTier.MEDIUM,
            assurance_level=AssuranceLevel.A2_INDEPENDENT,
            depends_on=["eip-b"],
            cost_usd=0.01,
        ),
        ChildEIPRef(
            eip_name="eip-d",
            eip_digest="d" * 64,
            risk_tier=RiskTier.HIGH,
            assurance_level=AssuranceLevel.A3_ATTESTED,
            depends_on=["eip-c"],
            cost_usd=0.01,
        ),
    ]
    _register_three_tier_dag(
        service,
        objective_id="eo-fault-3-budget",
        require_coordinated_release=True,
        max_total_cost_usd=0.10,
        child_eips=children,
    )

    specs = {
        "eip-a": ChildEIPExecutionSpec(
            eip_name="eip-a",
            change_request="Deliver eip-a within remaining budget.",
            cost_usd=0.08,
        ),
        "eip-b": ChildEIPExecutionSpec(
            eip_name="eip-b",
            change_request="Deliver eip-b exceeding remaining budget.",
            cost_usd=0.05,
        ),
        "eip-c": ChildEIPExecutionSpec(
            eip_name="eip-c",
            change_request="Downstream dependent of eip-b.",
            cost_usd=0.01,
        ),
        "eip-d": ChildEIPExecutionSpec(
            eip_name="eip-d",
            change_request="Transitive downstream dependent of eip-c.",
            human_approvers=["guardian-alice"],
            stage=PolicyStage.DEPLOY,
            cost_usd=0.01,
        ),
    }

    result = workflow.execute(
        HierarchicalDeliveryRequest(
            objective_id="eo-fault-3-budget",
            run_id_prefix="fault3-budget",
            child_specs=specs,
        )
    )

    assert result.executed_tranches == [["eip-a"]]
    assert "eip-a" in result.child_results
    assert "eip-b" not in result.child_results
    assert "eip-c" not in result.child_results
    assert "eip-d" not in result.child_results
    assert result.skipped_blocked_eips == {
        "eip-c": ["eip-b"],
        "eip-d": ["eip-c"],
    }
    assert result.cross_eip_proof_passed is False
    assert result.rollup_verified is False
    assert any("max_total_cost_usd" in reason for reason in result.rollup_reasons)
    assert result.release_readiness.can_release_objective is False
    assert result.final_objective.decomposition_status != DecompositionStatus.VERIFIED


def test_fault_4_leaf_evidence_staleness_or_failure_triggers_coordinated_rollback_or_hold(
    tmp_path: Path,
) -> None:
    _, _, service, workflow, _ = _build_hierarchical_workflow(
        tmp_path,
        db_name="fault4.sqlite3",
    )

    # Subcase A: Post-verification drift in Tranche 1 blocks Tranche 2 and holds verified sibling.
    _register_three_tier_dag(
        service,
        objective_id="eo-fault-4-drift",
        require_coordinated_release=True,
    )
    drift_result = workflow.execute(
        HierarchicalDeliveryRequest(
            objective_id="eo-fault-4-drift",
            run_id_prefix="fault4-drift",
            child_specs={
                "eip-a": ChildEIPExecutionSpec(
                    eip_name="eip-a",
                    change_request="Deliver eip-a with post-verify drift.",
                    simulate_drift_after_verify=True,
                ),
                "eip-b": ChildEIPExecutionSpec(
                    eip_name="eip-b",
                    change_request="Deliver healthy sibling eip-b.",
                ),
                "eip-c": ChildEIPExecutionSpec(
                    eip_name="eip-c",
                    change_request="Deliver Tranche 2 dependent eip-c.",
                    human_approvers=["guardian-alice"],
                    stage=PolicyStage.DEPLOY,
                ),
            },
        )
    )
    assert drift_result.executed_tranches == [["eip-a", "eip-b"]]
    assert drift_result.child_results["eip-a"].freshness_passed is False
    assert (
        drift_result.child_results["eip-a"].release_decision.outcome
        == ReleaseOutcome.BLOCKED_STALE_EVIDENCE
    )
    assert drift_result.child_results["eip-b"].release_decision.can_release is True
    assert drift_result.skipped_blocked_eips == {"eip-c": ["eip-a"]}
    assert "eip-c" not in drift_result.child_results
    assert drift_result.cross_eip_proof_passed is False
    assert drift_result.rollup_verified is False
    assert drift_result.release_readiness.can_release_objective is False
    assert drift_result.release_readiness.held_in_staging_eips == ["eip-b"]
    assert drift_result.release_readiness.rollback_required_eips == []
    assert drift_result.release_readiness.releasable_child_eips == []

    # Subcase B: Unrecoverable failure in Tranche 1 blocks Tranche 2 and requires sibling rollback.
    _register_three_tier_dag(
        service,
        objective_id="eo-fault-4-unrec",
        require_coordinated_release=True,
    )
    unrec_result = workflow.execute(
        HierarchicalDeliveryRequest(
            objective_id="eo-fault-4-unrec",
            run_id_prefix="fault4-unrec",
            child_specs={
                "eip-a": ChildEIPExecutionSpec(
                    eip_name="eip-a",
                    change_request="Deliver eip-a with unrecoverable failure.",
                    simulate_unrecoverable_failure=True,
                ),
                "eip-b": ChildEIPExecutionSpec(
                    eip_name="eip-b",
                    change_request="Deliver healthy sibling eip-b.",
                ),
                "eip-c": ChildEIPExecutionSpec(
                    eip_name="eip-c",
                    change_request="Deliver Tranche 2 dependent eip-c.",
                    human_approvers=["guardian-alice"],
                    stage=PolicyStage.DEPLOY,
                ),
            },
        )
    )
    assert unrec_result.executed_tranches == [["eip-b"]]
    assert "eip-a" not in unrec_result.child_results
    assert unrec_result.child_results["eip-b"].release_decision.can_release is True
    assert unrec_result.skipped_blocked_eips == {"eip-c": ["eip-a"]}
    assert "eip-c" not in unrec_result.child_results
    assert unrec_result.cross_eip_proof_passed is False
    assert unrec_result.rollup_verified is False
    assert unrec_result.release_readiness.can_release_objective is False
    assert unrec_result.release_readiness.rollback_required_eips == ["eip-b"]
    assert unrec_result.release_readiness.held_in_staging_eips == []
    assert unrec_result.release_readiness.releasable_child_eips == []


@pytest.mark.parametrize(
    ("domain", "target_resource", "summary", "payload", "rollback_plan"),
    [
        (
            DomainKind.INFRASTRUCTURE,
            "aws_security_group.prod_api",
            "Tighten production API ingress CIDR",
            {"environment": "production", "cidr_blocks": ["10.0.0.0/16"]},
            "Revert security group rule via Terraform state",
        ),
        (
            DomainKind.OPERATIONS,
            "k8s/deployment/prod-api",
            "Shift 20% production traffic to canary",
            {
                "environment": "production",
                "action_type": "traffic_shift",
                "slo_target": "p99 < 200ms",
                "health_check_probe": "http://127.0.0.1:8080/healthz",
            },
            "Shift 100% traffic back to stable production deployment",
        ),
        (
            DomainKind.BUSINESS,
            "pricing/enterprise-annual",
            "Adjust enterprise annual discount schedule",
            {
                "financial_impact_usd": 12000.0,
                "customer_segment": "enterprise",
                "policy_reference": "POL-FIN-2026-01",
            },
            "Restore prior enterprise pricing table snapshot",
        ),
        (
            DomainKind.COMPLIANCE,
            "control/soc2-access-review",
            "Record SOC2 production access review control",
            {
                "framework": "SOC2",
                "audit_ticket": "AUD-2026-500",
            },
            "Mark attestation superseded and reopen audit ticket",
        ),
    ],
)
def test_fault_5_scope_b_domain_pack_builder_self_verification_and_self_approval_denied(
    tmp_path: Path,
    domain: DomainKind,
    target_resource: str,
    summary: str,
    payload: dict[str, Any],
    rollback_plan: str,
) -> None:
    store = ServerStore(tmp_path / f"fault5-{domain.value}.sqlite3")
    key_provider = LocalEd25519KeyProvider.from_seed(
        _SEED_BYTES,
        key_id=f"ed25519-fault5-{domain.value}",
        key_dir=tmp_path / ".vad" / "keys",
    )
    runner = DomainPackExecutionRunner(
        store=store,
        memory_gateway=MemoryGateway(provider=NullMemoryProvider()),
        key_provider=key_provider,
    )
    builder_actor = f"{domain.value}-builder-01"

    with pytest.raises(PermissionError, match="distinct from builder_actor"):
        runner.execute(
            DomainExecutionRequest(
                run_id=f"fault5-self-verify-{domain.value}",
                domain=domain,
                target_resource=target_resource,
                summary=summary,
                payload=payload,
                rollback_plan=rollback_plan,
                builder_actor=builder_actor,
                verifier_actor=builder_actor,
            )
        )

    self_approved = runner.execute(
        DomainExecutionRequest(
            run_id=f"fault5-self-approve-{domain.value}",
            domain=domain,
            target_resource=target_resource,
            summary=summary,
            payload=payload,
            rollback_plan=rollback_plan,
            builder_actor=builder_actor,
            verifier_actor=f"{domain.value}-verifier-01",
            human_approvers=[builder_actor],
        )
    )
    assert self_approved.validation_errors == []
    assert self_approved.release_decision.risk_tier in {RiskTier.HIGH, RiskTier.CRITICAL}
    assert self_approved.release_decision.can_release is False
    assert self_approved.release_decision.outcome == ReleaseOutcome.DENIED_POLICY
    assert self_approved.applied is False
    assert self_approved.apply_receipt is None


def test_fault_6_remote_worker_denied_in_local_first_default_or_without_operator_intent(
    tmp_path: Path,
) -> None:
    default_policy = RemoteWorkerPoolPolicy()
    assert default_policy.mode == WorkerExecutionMode.LOCAL_FIRST
    assert default_policy.remote_workers_enabled is False

    store = ServerStore(tmp_path / "fault6.sqlite3")
    local_coordinator = RemoteWorkerCoordinator(policy=default_policy, store=store)
    worker = _make_remote_worker(endpoint_url="https://127.0.0.1:8443/verifier")

    admitted_ctrl, ctrl_reasons = RemoteWorkerAdmissionController(
        default_policy
    ).admit_worker(worker, operator_intent_ref="intent-fault6-001")
    assert admitted_ctrl is False
    assert any("disabled" in reason or "local-first" in reason for reason in ctrl_reasons)

    admitted_coord, coord_reasons = local_coordinator.register_worker(
        worker,
        operator_intent_ref="intent-fault6-001",
    )
    assert admitted_coord is False
    assert coord_reasons

    manifest, _ = _make_manifest_and_payloads(
        transfer_id="xfer-fault6-in",
        run_id="run-fault6",
        work_item_id="wi-fault6",
        fence_token=1,
        payloads={"inputs/patch.diff": b"diff\n"},
    )
    envelope = RemoteDispatchEnvelope(
        dispatch_id="disp-fault6",
        worker_id=worker.worker_id,
        run_id="run-fault6",
        work_item_id="wi-fault6",
        eip_digest=_sha256_hex(b"eip-fault6"),
        proof_plan_digest=_sha256_hex(b"plan-fault6"),
        risk_tier=RiskTier.HIGH,
        required_role=HostRole.VERIFIER,
        fence_token=1,
        operator_intent_ref="intent-fault6-001",
        input_manifest=manifest,
    )
    with pytest.raises(PermissionError, match="not registered"):
        local_coordinator.dispatch_work(envelope)

    # Even when hydrated from SQLite, LOCAL_FIRST policy blocks dispatch.
    store.save_remote_worker(worker, operator_intent_ref="intent-fault6-001")
    hydrated_local = RemoteWorkerCoordinator(policy=default_policy, store=store)
    with pytest.raises(PermissionError, match="disabled"):
        hydrated_local.dispatch_work(envelope)

    # Opt-in remote mode still rejects missing/blank operator_intent_ref and disallowed auth.
    opt_in_mtls_only = RemoteWorkerPoolPolicy(
        mode=WorkerExecutionMode.OPERATOR_HOSTED_REMOTE,
        remote_workers_enabled=True,
        require_operator_intent_for_remote=True,
        allowed_auth_methods=[WorkerAuthMethod.MTLS],
    )
    opt_in_coordinator = RemoteWorkerCoordinator(policy=opt_in_mtls_only)

    for bad_intent in (None, "", "   "):
        ok_intent, intent_reasons = opt_in_coordinator.register_worker(
            worker,
            operator_intent_ref=bad_intent,
        )
        assert ok_intent is False
        assert any("operator_intent_ref" in reason for reason in intent_reasons)

    oidc_worker = _make_remote_worker(
        worker_id="remote-oidc-01",
        auth_method=WorkerAuthMethod.OIDC,
        endpoint_url="https://localhost:8444/verifier",
    )
    ok_auth, auth_reasons = opt_in_coordinator.register_worker(
        oidc_worker,
        operator_intent_ref="intent-fault6-oidc",
    )
    assert ok_auth is False
    assert any("auth_method" in reason for reason in auth_reasons)


def test_fault_7_tampered_artifact_transfer_manifest_payload_or_stale_fence_token_rejected(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "fault7.sqlite3"
    store = ServerStore(db_path)
    workspace = _make_workspace(tmp_path)
    key_provider = LocalEd25519KeyProvider.from_seed(
        _SEED_BYTES,
        key_id="ed25519-fault7",
        key_dir=tmp_path / ".vad" / "keys",
    )
    workflow = VerifiedDeliveryWorkflow(
        store=store,
        key_provider=key_provider,
        workspace_dir=workspace,
        command_executor=lambda cmd_parts, **kwargs: subprocess.CompletedProcess(
            args=cmd_parts,
            returncode=0,
            stdout=f"ok: {' '.join(cmd_parts)}\n",
            stderr="",
        ),
    )

    run_id = "run-fault7"
    work_item_id = f"wi-{run_id}"
    delivery_res = workflow.execute(
        VerifiedDeliveryRequest(
            run_id=run_id,
            change_request="Verify remote artifact transfer and fence security.",
            eip_name="eip-fault7",
            risk_tier=RiskTier.LOW,
        )
    )
    assert delivery_res.audit_report.verified is True

    policy = RemoteWorkerPoolPolicy(
        mode=WorkerExecutionMode.OPERATOR_HOSTED_REMOTE,
        remote_workers_enabled=True,
        require_operator_intent_for_remote=True,
    )
    coordinator = RemoteWorkerCoordinator(
        policy=policy,
        session_manager=HostSessionManager(signing_secret=b"fault7-coordinator-secret-0123"),
        store=store,
    )
    worker = _make_remote_worker(endpoint_url="http://127.0.0.1:8085/verifier")
    admitted, _ = coordinator.register_worker(worker, operator_intent_ref="intent-fault7")
    assert admitted is True

    in_manifest_1, in_payloads_1 = _make_manifest_and_payloads(
        transfer_id="xfer-in-gen1",
        run_id=run_id,
        work_item_id=work_item_id,
        fence_token=1,
        payloads={"inputs/patch.diff": b"--- a/app.py\n+++ b/app.py\n"},
    )
    env_1 = RemoteDispatchEnvelope(
        dispatch_id="disp-gen1",
        worker_id=worker.worker_id,
        run_id=run_id,
        work_item_id=work_item_id,
        eip_digest=_sha256_hex(b"eip-fault7"),
        proof_plan_digest=_sha256_hex(b"plan-fault7"),
        risk_tier=RiskTier.HIGH,
        required_role=HostRole.VERIFIER,
        fence_token=1,
        operator_intent_ref="intent-fault7",
        input_manifest=in_manifest_1,
    )

    # Coordinator rejects tampered input payload and unlisted extra input file.
    with pytest.raises(ValueError, match="digest mismatch"):
        coordinator.dispatch_work(
            env_1,
            input_payloads={"inputs/patch.diff": b"tampered\n"},
            builder_actor="builder-01",
        )
    with pytest.raises(ValueError, match="unexpected unlisted artifact payload"):
        coordinator.dispatch_work(
            env_1,
            input_payloads={**in_payloads_1, "inputs/sneaky.sh": b"echo pwn\n"},
            builder_actor="builder-01",
        )

    # Valid gen 1 dispatch followed by gen 2 lease generation increment.
    coordinator.dispatch_work(env_1, input_payloads=in_payloads_1, builder_actor="builder-01")
    in_manifest_2, in_payloads_2 = _make_manifest_and_payloads(
        transfer_id="xfer-in-gen2",
        run_id=run_id,
        work_item_id=work_item_id,
        fence_token=2,
        payloads={"inputs/patch.diff": b"--- a/app.py\n+++ b/app.py\n"},
    )
    env_2 = env_1.model_copy(
        update={
            "dispatch_id": "disp-gen2",
            "fence_token": 2,
            "input_manifest": in_manifest_2,
        }
    )
    coordinator.dispatch_work(env_2, input_payloads=in_payloads_2, builder_actor="builder-01")

    # Stale receipt carrying fence_token=1 after generation increment to 2 is rejected.
    stale_out_manifest, stale_out_payloads = _make_manifest_and_payloads(
        transfer_id="xfer-out-stale",
        run_id=run_id,
        work_item_id=work_item_id,
        fence_token=1,
        payloads={"reports/result.json": b'{"passed":true}\n'},
    )
    stale_receipt = RemoteExecutionReceipt(
        receipt_id="rcpt-stale",
        dispatch_id="disp-gen1",
        worker_id=worker.worker_id,
        run_id=run_id,
        work_item_id=work_item_id,
        fence_token=1,
        actor=worker.worker_id,
        role=HostRole.VERIFIER,
        passed=True,
        summary="Stale lease verification",
        output_manifest=stale_out_manifest,
    )
    stale_res = coordinator.ingest_execution_receipt(
        stale_receipt,
        output_payloads=stale_out_payloads,
    )
    assert stale_res.verified is False
    assert any("active_fence_token" in reason for reason in stale_res.reasons)

    # Active gen 2 receipt rejects tampered output bytes and unlisted extra output files.
    out_manifest_2, out_payloads_2 = _make_manifest_and_payloads(
        transfer_id="xfer-out-gen2",
        run_id=run_id,
        work_item_id=work_item_id,
        fence_token=2,
        payloads={"reports/result.json": b'{"passed":true}\n'},
    )
    receipt_2 = RemoteExecutionReceipt(
        receipt_id="rcpt-gen2",
        dispatch_id="disp-gen2",
        worker_id=worker.worker_id,
        run_id=run_id,
        work_item_id=work_item_id,
        fence_token=2,
        actor=worker.worker_id,
        role=HostRole.VERIFIER,
        passed=True,
        summary="Gen 2 verification",
        output_manifest=out_manifest_2,
    )
    tampered_out_res = coordinator.ingest_execution_receipt(
        receipt_2,
        output_payloads={"reports/result.json": b'{"passed":false}\n'},
    )
    assert tampered_out_res.verified is False
    assert any("digest mismatch" in reason for reason in tampered_out_res.reasons)

    extra_out_res = coordinator.ingest_execution_receipt(
        receipt_2,
        output_payloads={**out_payloads_2, "reports/unlisted.txt": b"extra\n"},
    )
    assert extra_out_res.verified is False
    assert any(
        "unexpected unlisted artifact payload" in reason for reason in extra_out_res.reasons
    )

    # Persist valid gen 2 receipt, then verify audit trail rejection on tampering/extra/stale fence.
    ok_out_res = coordinator.ingest_execution_receipt(
        receipt_2,
        output_payloads=out_payloads_2,
    )
    assert ok_out_res.verified is True

    valid_payloads = {
        "xfer-in-gen1": in_payloads_1,
        "xfer-in-gen2": in_payloads_2,
        "xfer-out-gen2": out_payloads_2,
    }
    clean_audit = verify_run_audit_trail(
        store,
        run_id,
        key_provider=key_provider,
        artifact_payloads=valid_payloads,
        require_transfer_manifests=True,
    )
    assert clean_audit.verified is True

    tampered_audit = verify_run_audit_trail(
        store,
        run_id,
        key_provider=key_provider,
        artifact_payloads={
            **valid_payloads,
            "xfer-out-gen2": {"reports/result.json": b'{"passed":false}\n'},
        },
        require_transfer_manifests=True,
    )
    assert tampered_audit.verified is False
    assert any("digest mismatch" in failure for failure in tampered_audit.failures)

    extra_audit = verify_run_audit_trail(
        store,
        run_id,
        key_provider=key_provider,
        artifact_payloads={
            **valid_payloads,
            "xfer-out-gen2": {**out_payloads_2, "reports/unlisted.txt": b"extra\n"},
        },
        require_transfer_manifests=True,
    )
    assert extra_audit.verified is False
    assert any(
        "unexpected unlisted artifact payload" in failure
        for failure in extra_audit.failures
    )

    # Stale/invalid fence_token tampered in SQLite is caught by verify_run_audit_trail.
    bad_manifest = out_manifest_2.model_dump(mode="json")
    bad_manifest["fence_token"] = 0
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE artifact_transfer_manifests SET fence_token = 0, payload_json = ? WHERE transfer_id = ?",
            (json.dumps(bad_manifest, sort_keys=True), out_manifest_2.transfer_id),
        )
    stale_fence_audit = verify_run_audit_trail(
        store,
        run_id,
        key_provider=key_provider,
        require_transfer_manifests=True,
    )
    assert stale_fence_audit.verified is False
    assert any("fence_token" in failure for failure in stale_fence_audit.failures)


def test_fault_8_unauthorized_enterprise_rbac_group_and_builder_in_rbac_group_denied(
    tmp_path: Path,
) -> None:
    store = ServerStore(tmp_path / "fault8.sqlite3")
    policy = RemoteWorkerPoolPolicy(
        mode=WorkerExecutionMode.OPERATOR_HOSTED_REMOTE,
        remote_workers_enabled=True,
        rbac_release_guardian_groups=["release-guardians", "sec-approvers"],
    )
    coordinator = RemoteWorkerCoordinator(policy=policy, store=store)

    # Guardian outside configured rbac_release_guardian_groups is denied.
    unauthorized_group = coordinator.authorize_release_with_rbac(
        actor_groups_by_guardian={"guardian-outsider": ["readonly-viewers"]},
        risk_tier=RiskTier.HIGH,
        builder_actor="builder-bob",
        verifier_actor="verifier-01",
    )
    assert unauthorized_group.authorized is False
    assert unauthorized_group.approved_guardians == []
    assert unauthorized_group.denied_actors == ["guardian-outsider"]
    assert any("RBAC" in reason for reason in unauthorized_group.reasons)

    # Empty rbac_release_guardian_groups fails closed even if actor presents groups.
    empty_rbac_coordinator = RemoteWorkerCoordinator(
        policy=RemoteWorkerPoolPolicy(
            mode=WorkerExecutionMode.OPERATOR_HOSTED_REMOTE,
            remote_workers_enabled=True,
            rbac_release_guardian_groups=[],
        ),
        store=store,
    )
    no_configured_groups = empty_rbac_coordinator.authorize_release_with_rbac(
        actor_groups_by_guardian={"guardian-alice": ["release-guardians"]},
        risk_tier=RiskTier.HIGH,
        builder_actor="builder-bob",
        verifier_actor="verifier-01",
    )
    assert no_configured_groups.authorized is False
    assert no_configured_groups.denied_actors == ["guardian-alice"]

    # CRITICAL release with fewer than 2 distinct authorized RBAC guardians is denied.
    critical_single = coordinator.authorize_release_with_rbac(
        actor_groups_by_guardian={"guardian-alice": ["release-guardians"]},
        risk_tier=RiskTier.CRITICAL,
        builder_actor="builder-bob",
        verifier_actor="verifier-01",
    )
    assert critical_single.authorized is False
    assert critical_single.approved_guardians == ["guardian-alice"]
    assert any("at least 2" in reason for reason in critical_single.reasons)

    # Builder attempting to act as RBAC guardian is denied even with authorized RBAC groups.
    builder_in_rbac = coordinator.authorize_release_with_rbac(
        actor_groups_by_guardian={
            "builder-bob": ["release-guardians", "sec-approvers"],
            "guardian-alice": ["release-guardians"],
        },
        risk_tier=RiskTier.HIGH,
        builder_actor="builder-bob",
        verifier_actor="verifier-01",
    )
    assert builder_in_rbac.authorized is False
    assert builder_in_rbac.denied_actors == ["builder-bob"]
    assert builder_in_rbac.approved_guardians == ["guardian-alice"]
    assert any("separation of duties" in reason for reason in builder_in_rbac.reasons)

    # Even for CRITICAL with 2 other valid guardians, builder acting as guardian denies release.
    builder_plus_two = coordinator.authorize_release_with_rbac(
        actor_groups_by_guardian={
            "builder-bob": ["release-guardians"],
            "guardian-alice": ["release-guardians"],
            "guardian-carol": ["sec-approvers"],
        },
        risk_tier=RiskTier.CRITICAL,
        builder_actor="builder-bob",
        verifier_actor="verifier-01",
    )
    assert builder_plus_two.authorized is False
    assert builder_plus_two.denied_actors == ["builder-bob"]
    assert builder_plus_two.approved_guardians == ["guardian-alice", "guardian-carol"]
