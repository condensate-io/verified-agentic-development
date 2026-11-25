from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
from pathlib import Path
from typing import Any

from vad.audit.verify_trail import (
    AuditTrailReport,
    ObjectiveAuditTrailReport,
    verify_objective_audit_trail,
    verify_run_audit_trail,
)
from vad.contracts.intent_hierarchy import (
    ChildEIPRef,
    DecompositionStatus,
    EngineeringObjective,
    Programme,
    StrategicObjective,
)
from vad.contracts.models import AssuranceLevel, RiskTier
from vad.control_plane.intent_hierarchy_service import IntentHierarchyService
from vad.control_plane.remote_workers import ArtifactTransferManifest
from vad.server.db.store import ServerStore
from vad.signing.ed25519_local import LocalEd25519KeyProvider
from vad.workflows.verified_delivery import (
    VerifiedDeliveryRequest,
    VerifiedDeliveryWorkflow,
)

_SEED_BYTES = bytes.fromhex(
    "9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60"
)


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _passing_executor(cmd_parts: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        args=cmd_parts,
        returncode=0,
        stdout=f"ok: {' '.join(cmd_parts)}\n",
        stderr="",
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


def _check_map(report: AuditTrailReport | ObjectiveAuditTrailReport) -> dict[str, bool]:
    return {item.check: item.passed for item in report.checks}


def _seed_verified_objective_hierarchy(
    tmp_path: Path,
    *,
    db_name: str = "objective_audit.sqlite3",
    run_id_prefix: str = "obj-run",
) -> tuple[
    ServerStore,
    LocalEd25519KeyProvider,
    StrategicObjective,
    Programme,
    EngineeringObjective,
    dict[str, str],
]:
    db_path = tmp_path / db_name
    store = ServerStore(db_path)
    workspace = _make_workspace(tmp_path)
    key_provider = LocalEd25519KeyProvider.from_seed(_SEED_BYTES, key_id="ed25519-v3-test")
    workflow = VerifiedDeliveryWorkflow(
        store=store,
        key_provider=key_provider,
        workspace_dir=workspace,
        command_executor=_passing_executor,
    )

    svc = IntentHierarchyService(store=store)
    strategic = StrategicObjective(
        node_id="so-platform-trust",
        title="Platform Trust",
        description="Maintain attested verification across all deliveries.",
        risk_floor=RiskTier.MEDIUM,
        assurance_floor=AssuranceLevel.A2_INDEPENDENT,
        inherited_invariants=["verifier_not_builder", "no_plaintext_secrets"],
        max_total_cost_usd=200.0,
    )
    programme = Programme(
        node_id="prog-audit-v3",
        title="Audit & Rollup Programme",
        description="Coordinate multi-EIP verified delivery.",
        parent_id=strategic.node_id,
        risk_floor=RiskTier.MEDIUM,
        assurance_floor=AssuranceLevel.A2_INDEPENDENT,
        inherited_invariants=["verifier_not_builder", "no_plaintext_secrets", "immutable_ledger"],
        max_total_cost_usd=120.0,
    )
    engineering = EngineeringObjective(
        node_id="eo-multi-eip-delivery",
        title="Multi-EIP Verified Delivery",
        description="Deliver schema and policy enforcement EIPs in order.",
        parent_id=programme.node_id,
        risk_floor=RiskTier.MEDIUM,
        assurance_floor=AssuranceLevel.A2_INDEPENDENT,
        inherited_invariants=["verifier_not_builder", "no_plaintext_secrets", "immutable_ledger"],
        max_total_cost_usd=80.0,
        child_eips=[
            ChildEIPRef(
                eip_name="eip-schema",
                eip_digest="a" * 64,
                risk_tier=RiskTier.MEDIUM,
                assurance_level=AssuranceLevel.A2_INDEPENDENT,
                cost_usd=10.0,
            ),
            ChildEIPRef(
                eip_name="eip-enforcer",
                eip_digest="b" * 64,
                risk_tier=RiskTier.HIGH,
                assurance_level=AssuranceLevel.A3_ATTESTED,
                depends_on=["eip-schema"],
                cost_usd=15.0,
            ),
        ],
    )

    svc.register_strategic_objective(strategic)
    svc.register_programme(programme)
    svc.register_engineering_objective(engineering)
    svc.submit_for_approval(engineering.node_id)
    svc.approve_objective(
        engineering.node_id,
        approver="guardian-alice",
        approver_role="release_guardian",
    )

    child_run_ids: dict[str, str] = {}
    for child in engineering.child_eips:
        run_id = f"{run_id_prefix}-{child.eip_name}"
        child_run_ids[child.eip_name] = run_id
        approvers = ["guardian-alice"] if child.risk_tier == RiskTier.HIGH else []
        result = workflow.execute(
            VerifiedDeliveryRequest(
                run_id=run_id,
                change_request=f"Implement {child.eip_name} module safely",
                eip_name=child.eip_name,
                risk_tier=child.risk_tier,
                human_approvers=approvers,
                cost_usd=child.cost_usd,
            )
        )
        assert result.audit_report.verified is True
        svc.record_leaf_outcome(
            engineering.node_id,
            eip_name=child.eip_name,
            verified=result.verification_passed and result.freshness_passed,
            evidence_fresh=result.freshness_passed,
            evidence_digest=result.evidence_digest,
            cost_usd=child.cost_usd,
        )

    final_objective = svc.record_cross_eip_proof(engineering.node_id, passed=True)
    assert final_objective.decomposition_status == DecompositionStatus.VERIFIED
    return store, key_provider, strategic, programme, final_objective, child_run_ids


def test_verify_objective_audit_trail_passes_three_tier_hierarchy(tmp_path: Path) -> None:
    store, key_provider, _, _, objective, _ = _seed_verified_objective_hierarchy(tmp_path)

    report = verify_objective_audit_trail(
        store,
        objective.node_id,
        key_provider=key_provider,
    )

    assert report.objective_id == objective.node_id
    assert report.verified is True
    assert report.decomposition_status == DecompositionStatus.VERIFIED.value
    assert report.failures == []
    assert [item.check for item in report.checks] == [
        "objective_loaded",
        "hierarchy_chain_floors",
        "decomposition_approved",
        "objective_rollup",
        "child_run_trails",
    ]
    assert all(item.passed for item in report.checks)
    assert set(report.child_reports.keys()) == {"eip-schema", "eip-enforcer"}
    assert all(child_rep.verified for child_rep in report.child_reports.values())
    for child in objective.child_eips:
        assert report.child_reports[child.eip_name].evidence_digest == child.evidence_digest


def test_verify_objective_audit_trail_supports_custom_child_run_ids(tmp_path: Path) -> None:
    store, key_provider, _, _, objective, child_run_ids = _seed_verified_objective_hierarchy(
        tmp_path,
        run_id_prefix="custom-exec",
    )

    # Without custom child_run_ids / run_id_prefix, default prefix 'obj-run' fails closed.
    default_report = verify_objective_audit_trail(
        store,
        objective.node_id,
        key_provider=key_provider,
    )
    assert default_report.verified is False
    assert _check_map(default_report)["child_run_trails"] is False

    # Passing explicit child_run_ids resolves and verifies all child runs.
    explicit_report = verify_objective_audit_trail(
        store,
        objective.node_id,
        child_run_ids=child_run_ids,
        key_provider=key_provider,
    )
    assert explicit_report.verified is True
    assert explicit_report.failures == []


def test_verify_objective_audit_trail_missing_or_wrong_level_fails_closed(tmp_path: Path) -> None:
    store, key_provider, _, programme, _, _ = _seed_verified_objective_hierarchy(tmp_path)

    missing_report = verify_objective_audit_trail(
        store,
        "eo-does-not-exist",
        key_provider=key_provider,
    )
    assert missing_report.verified is False
    assert missing_report.decomposition_status is None
    assert _check_map(missing_report)["objective_loaded"] is False
    assert any("objective_loaded" in failure for failure in missing_report.failures)

    programme_report = verify_objective_audit_trail(
        store,
        programme.node_id,
        key_provider=key_provider,
    )
    assert programme_report.verified is False
    assert _check_map(programme_report)["objective_loaded"] is False
    assert any("engineering_objective" in failure for failure in programme_report.failures)


def test_verify_objective_audit_trail_tampered_parent_floors_or_budget_fails(
    tmp_path: Path,
) -> None:
    store, key_provider, strategic, programme, objective, _ = _seed_verified_objective_hierarchy(
        tmp_path
    )

    # 1. Tamper Programme risk_floor above EngineeringObjective & child EIPs in SQLite.
    tampered_prog = programme.model_copy(update={"risk_floor": RiskTier.CRITICAL})
    store.save_intent_hierarchy_node(tampered_prog)

    prog_tamper_report = verify_objective_audit_trail(
        store,
        objective.node_id,
        key_provider=key_provider,
    )
    assert prog_tamper_report.verified is False
    assert _check_map(prog_tamper_report)["hierarchy_chain_floors"] is False
    assert any("risk_floor" in failure for failure in prog_tamper_report.failures)

    # Restore Programme, then tamper StrategicObjective assurance_floor above Programme/child.
    store.save_intent_hierarchy_node(programme)
    tampered_strategic = strategic.model_copy(
        update={"assurance_floor": AssuranceLevel.A4_RESTRICTED}
    )
    store.save_intent_hierarchy_node(tampered_strategic)

    strategic_tamper_report = verify_objective_audit_trail(
        store,
        objective.node_id,
        key_provider=key_provider,
    )
    assert strategic_tamper_report.verified is False
    assert _check_map(strategic_tamper_report)["hierarchy_chain_floors"] is False
    assert any("assurance_floor" in failure for failure in strategic_tamper_report.failures)

    # Restore StrategicObjective, then lower EngineeringObjective budget below child costs.
    store.save_intent_hierarchy_node(strategic)
    over_budget_obj = objective.model_copy(update={"max_total_cost_usd": 5.0})
    store.save_intent_hierarchy_node(over_budget_obj)

    budget_report = verify_objective_audit_trail(
        store,
        objective.node_id,
        key_provider=key_provider,
    )
    assert budget_report.verified is False
    assert _check_map(budget_report)["hierarchy_chain_floors"] is False
    assert any("max_total_cost_usd" in failure for failure in budget_report.failures)


def test_verify_objective_audit_trail_unapproved_or_missing_approver_fails(
    tmp_path: Path,
) -> None:
    store, key_provider, _, _, objective, _ = _seed_verified_objective_hierarchy(tmp_path)

    # 1. Status reverted to DRAFT in SQLite.
    draft_obj = objective.model_copy(
        update={"decomposition_status": DecompositionStatus.DRAFT}
    )
    store.save_intent_hierarchy_node(draft_obj)
    draft_report = verify_objective_audit_trail(
        store,
        objective.node_id,
        key_provider=key_provider,
    )
    assert draft_report.verified is False
    assert _check_map(draft_report)["decomposition_approved"] is False

    # 2. Status VERIFIED but approved_by stripped in SQLite.
    missing_approver_obj = objective.model_copy(update={"approved_by": "   "})
    store.save_intent_hierarchy_node(missing_approver_obj)
    approver_report = verify_objective_audit_trail(
        store,
        objective.node_id,
        key_provider=key_provider,
    )
    assert approver_report.verified is False
    assert _check_map(approver_report)["decomposition_approved"] is False
    assert any("approved_by" in failure for failure in approver_report.failures)


def test_verify_objective_audit_trail_failed_cross_eip_proof_and_digest_mismatch_fail(
    tmp_path: Path,
) -> None:
    store, key_provider, _, _, objective, _ = _seed_verified_objective_hierarchy(tmp_path)

    # 1. cross_eip_proof_passed is False.
    no_cross_proof = objective.model_copy(update={"cross_eip_proof_passed": False})
    store.save_intent_hierarchy_node(no_cross_proof)
    cross_report = verify_objective_audit_trail(
        store,
        objective.node_id,
        key_provider=key_provider,
    )
    assert cross_report.verified is False
    assert _check_map(cross_report)["objective_rollup"] is False

    # 2. Child EIP stored evidence_digest does not match actual child run evidence in SQLite.
    tampered_children = [
        objective.child_eips[0].model_copy(update={"evidence_digest": "f" * 64}),
        *objective.child_eips[1:],
    ]
    mismatch_obj = objective.model_copy(update={"child_eips": tampered_children})
    store.save_intent_hierarchy_node(mismatch_obj)
    mismatch_report = verify_objective_audit_trail(
        store,
        objective.node_id,
        key_provider=key_provider,
    )
    assert mismatch_report.verified is False
    assert _check_map(mismatch_report)["child_run_trails"] is False
    assert any("evidence_digest mismatch" in failure for failure in mismatch_report.failures)


def test_verify_run_audit_trail_with_artifact_transfer_manifests(tmp_path: Path) -> None:
    db_path = tmp_path / "run_manifests.sqlite3"
    store = ServerStore(db_path)
    workspace = _make_workspace(tmp_path)
    key_provider = LocalEd25519KeyProvider.from_seed(_SEED_BYTES, key_id="ed25519-xfer-test")
    workflow = VerifiedDeliveryWorkflow(
        store=store,
        key_provider=key_provider,
        workspace_dir=workspace,
        command_executor=_passing_executor,
    )

    run_id = "run-with-manifest"
    result = workflow.execute(
        VerifiedDeliveryRequest(
            run_id=run_id,
            change_request="Verify remote artifact transfer manifest audit check",
            eip_name="eip-remote-xfer",
            risk_tier=RiskTier.LOW,
        )
    )
    assert result.audit_report.verified is True
    assert len(result.audit_report.checks) == 8

    # Requiring transfer manifests when none exist fails closed.
    required_missing = verify_run_audit_trail(
        store,
        run_id,
        key_provider=key_provider,
        require_transfer_manifests=True,
    )
    assert required_missing.verified is False
    assert _check_map(required_missing)["artifact_transfer_manifests"] is False

    # Save valid ArtifactTransferManifest for run_id.
    payloads = {
        "outputs/patch.diff": b"--- a/app.py\n+++ b/app.py\n",
        "outputs/report.json": b'{"passed":true}\n',
    }
    manifest = ArtifactTransferManifest(
        transfer_id="xfer-run-001",
        run_id=run_id,
        work_item_id=f"wi-{run_id}",
        fence_token=2,
        artifacts={path: _sha256_hex(content) for path, content in payloads.items()},
    )
    store.save_artifact_transfer_manifest(manifest)

    # Structural check passes automatically when manifests are recorded.
    report_structural = verify_run_audit_trail(
        store,
        run_id,
        key_provider=key_provider,
    )
    assert report_structural.verified is True
    assert len(report_structural.checks) == 9
    assert _check_map(report_structural)["artifact_transfer_manifests"] is True

    # Matching payload bytes pass bundle verification.
    report_matching = verify_run_audit_trail(
        store,
        run_id,
        key_provider=key_provider,
        artifact_payloads={manifest.transfer_id: payloads},
    )
    assert report_matching.verified is True
    assert _check_map(report_matching)["artifact_transfer_manifests"] is True

    # Tampered payload bytes fail bundle verification.
    tampered_payloads = {
        **payloads,
        "outputs/report.json": b'{"passed":false}\n',
    }
    report_tampered = verify_run_audit_trail(
        store,
        run_id,
        key_provider=key_provider,
        artifact_payloads={manifest.transfer_id: tampered_payloads},
    )
    assert report_tampered.verified is False
    assert _check_map(report_tampered)["artifact_transfer_manifests"] is False
    assert any("digest mismatch" in failure for failure in report_tampered.failures)

    # Missing file payload or missing transfer_id fails.
    report_missing_file = verify_run_audit_trail(
        store,
        run_id,
        key_provider=key_provider,
        artifact_payloads={manifest.transfer_id: {"outputs/patch.diff": payloads["outputs/patch.diff"]}},
    )
    assert report_missing_file.verified is False
    assert _check_map(report_missing_file)["artifact_transfer_manifests"] is False

    report_missing_xfer = verify_run_audit_trail(
        store,
        run_id,
        key_provider=key_provider,
        artifact_payloads={},
    )
    assert report_missing_xfer.verified is False
    assert _check_map(report_missing_xfer)["artifact_transfer_manifests"] is False

    # Tampered manifest in SQLite (invalid fence_token=0) fails closed.
    bad_payload = manifest.model_dump(mode="json")
    bad_payload["fence_token"] = 0
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE artifact_transfer_manifests SET fence_token = 0, payload_json = ? WHERE transfer_id = ?",
            (json.dumps(bad_payload, sort_keys=True), manifest.transfer_id),
        )
    report_bad_db = verify_run_audit_trail(
        store,
        run_id,
        key_provider=key_provider,
    )
    assert report_bad_db.verified is False
    assert _check_map(report_bad_db)["artifact_transfer_manifests"] is False
