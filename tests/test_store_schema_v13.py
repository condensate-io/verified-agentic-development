import json
import sqlite3
from datetime import datetime, timezone

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
from vad.control_plane.remote_workers import (
    ArtifactTransferManifest,
    RemoteWorkerIdentity,
    WorkerAuthMethod,
)
from vad.control_plane.sessions import HostRole
from vad.policy.autonomy_matrix import PolicyStage
from vad.release.decision import ReleaseDecision, ReleaseOutcome
from vad.server.db.store import (
    SCHEMA_VERSION,
    ServerStore,
    StoredDomainProposalRecord,
)


def test_fresh_migration_reaches_v13_and_is_idempotent(tmp_path):
    db_path = tmp_path / "fresh_v13.sqlite3"
    store = ServerStore(db_path)

    store.migrate()
    store.migrate()

    with sqlite3.connect(db_path) as conn:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        intent_cols = {
            row[1]
            for row in conn.execute("PRAGMA table_info(intent_hierarchy_nodes)").fetchall()
        }
        proposal_cols = {
            row[1]
            for row in conn.execute("PRAGMA table_info(domain_proposals)").fetchall()
        }
        worker_cols = {
            row[1]
            for row in conn.execute("PRAGMA table_info(remote_workers)").fetchall()
        }
        manifest_cols = {
            row[1]
            for row in conn.execute("PRAGMA table_info(artifact_transfer_manifests)").fetchall()
        }

    assert SCHEMA_VERSION == 13
    assert version == 13
    assert {
        "intent_hierarchy_nodes",
        "domain_proposals",
        "remote_workers",
        "artifact_transfer_manifests",
    }.issubset(tables)
    assert intent_cols == {
        "node_id",
        "level",
        "parent_id",
        "title",
        "risk_floor",
        "assurance_floor",
        "decomposition_status",
        "payload_json",
        "updated_at",
    }
    assert proposal_cols == {
        "proposal_id",
        "run_id",
        "domain",
        "target_resource",
        "payload_digest",
        "decision_id",
        "can_release",
        "applied",
        "payload_json",
        "created_at",
    }
    assert worker_cols == {
        "worker_id",
        "host_type",
        "role",
        "auth_method",
        "endpoint_url",
        "operator_intent_ref",
        "payload_json",
        "registered_at",
    }
    assert manifest_cols == {
        "transfer_id",
        "run_id",
        "work_item_id",
        "fence_token",
        "payload_json",
        "recorded_at",
    }


def test_upgrade_from_v12_preserves_existing_data_and_reaches_v13(tmp_path):
    db_path = tmp_path / "upgrade_v12.sqlite3"
    store = ServerStore(db_path)

    v12_decision = ReleaseDecision(
        decision_id="dec-v12-1",
        run_id="run-v12",
        eip_digest="a" * 64,
        evidence_digest="b" * 64,
        risk_tier=RiskTier.LOW,
        stage=PolicyStage.BUILD,
        assurance_required=AssuranceLevel.A1_BASIC,
        assurance_achieved=AssuranceLevel.A1_BASIC,
        outcome=ReleaseOutcome.AUTONOMOUS_RELEASE,
        can_release=True,
        reasons=["v12 baseline"],
        required_signoffs=0,
        decided_at=datetime(2026, 7, 15, 10, 0, tzinfo=timezone.utc),
    )

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        store._migrate_001(conn)
        store._migrate_002(conn)
        store._migrate_003(conn)
        store._migrate_004(conn)
        store._migrate_005(conn)
        store._migrate_006(conn)
        store._migrate_007(conn)
        store._migrate_008(conn)
        store._migrate_009(conn)
        store._migrate_010(conn)
        store._migrate_011(conn)
        store._migrate_012(conn)
        conn.execute(
            """
            INSERT INTO release_decisions(
                decision_id, run_id, eip_digest, evidence_digest, risk_tier,
                stage, outcome, can_release, payload_json, decided_at
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                v12_decision.decision_id,
                v12_decision.run_id,
                v12_decision.eip_digest,
                v12_decision.evidence_digest,
                v12_decision.risk_tier.value,
                v12_decision.stage.value,
                v12_decision.outcome.value,
                1,
                json.dumps(v12_decision.model_dump(mode="json"), sort_keys=True),
                v12_decision.decided_at.isoformat(),
            ),
        )
        conn.execute("PRAGMA user_version = 12")

    reopened = ServerStore(db_path)
    reopened.migrate()

    with sqlite3.connect(db_path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 13
        reopened._migrate_013(conn)
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }

    assert {
        "intent_hierarchy_nodes",
        "domain_proposals",
        "remote_workers",
        "artifact_transfer_manifests",
    }.issubset(tables)
    assert reopened.list_release_decisions("run-v12") == [v12_decision]


def test_intent_hierarchy_nodes_crud_and_filtering(tmp_path):
    db_path = tmp_path / "intent_hierarchy.sqlite3"
    store = ServerStore(db_path)

    with pytest.raises(KeyError):
        store.load_intent_hierarchy_node("missing-node")

    strategic = StrategicObjective(
        node_id="so-1",
        title="Zero-Trust Control Plane",
        description="Enforce strict provenance and separation of duties.",
        risk_floor=RiskTier.HIGH,
        assurance_floor=AssuranceLevel.A3_ATTESTED,
        inherited_invariants=["inv-no-self-approval", "inv-signed-evidence"],
        max_total_cost_usd=500.0,
        decomposition_status=DecompositionStatus.APPROVED,
        approved_by="principal-alice",
    )
    programme = Programme(
        node_id="prog-1",
        parent_id="so-1",
        title="Persistence & Schema Hardening",
        description="Upgrade durable storage for v3 workflows.",
        risk_floor=RiskTier.HIGH,
        assurance_floor=AssuranceLevel.A3_ATTESTED,
        inherited_invariants=["inv-no-self-approval", "inv-signed-evidence"],
        max_total_cost_usd=200.0,
        decomposition_status=DecompositionStatus.EXECUTING,
        approved_by="programme-bob",
    )
    eng_1 = EngineeringObjective(
        node_id="eo-1",
        parent_id="prog-1",
        title="Schema v13 Tables",
        description="Persist hierarchy, domain proposals, remote workers, and manifests.",
        risk_floor=RiskTier.HIGH,
        assurance_floor=AssuranceLevel.A3_ATTESTED,
        inherited_invariants=["inv-no-self-approval", "inv-signed-evidence"],
        max_total_cost_usd=75.0,
        decomposition_status=DecompositionStatus.EXECUTING,
        approved_by="guardian-carol",
        child_eips=[
            ChildEIPRef(
                eip_name="eip-schema-v13",
                eip_digest="a" * 64,
                risk_tier=RiskTier.HIGH,
                assurance_level=AssuranceLevel.A3_ATTESTED,
                depends_on=[],
                verified=True,
                evidence_fresh=True,
                evidence_digest="b" * 64,
                cost_usd=12.5,
            ),
            ChildEIPRef(
                eip_name="eip-store-crud",
                eip_digest="c" * 64,
                risk_tier=RiskTier.CRITICAL,
                assurance_level=AssuranceLevel.A4_RESTRICTED,
                depends_on=["eip-schema-v13"],
                verified=False,
                evidence_fresh=False,
                cost_usd=20.0,
            ),
        ],
        cross_eip_proof_passed=False,
        require_coordinated_release=True,
    )
    eng_2 = EngineeringObjective(
        node_id="eo-2",
        parent_id="prog-other",
        title="Unrelated Objective",
        description="Lives under a different programme.",
        risk_floor=RiskTier.MEDIUM,
        assurance_floor=AssuranceLevel.A2_INDEPENDENT,
        decomposition_status=DecompositionStatus.DRAFT,
    )

    store.save_intent_hierarchy_node(strategic)
    store.save_intent_hierarchy_node(programme)
    store.save_intent_hierarchy_node(eng_1)
    store.save_intent_hierarchy_node(eng_2)

    reopened = ServerStore(db_path)
    loaded_so = reopened.load_intent_hierarchy_node("so-1")
    loaded_prog = reopened.load_intent_hierarchy_node("prog-1")
    loaded_eo = reopened.load_intent_hierarchy_node("eo-1")

    assert isinstance(loaded_so, StrategicObjective)
    assert isinstance(loaded_prog, Programme)
    assert isinstance(loaded_eo, EngineeringObjective)
    assert loaded_so == strategic
    assert loaded_prog == programme
    assert loaded_eo == eng_1
    assert len(loaded_eo.child_eips) == 2
    assert loaded_eo.child_eips[1].depends_on == ["eip-schema-v13"]
    assert loaded_eo.require_coordinated_release is True

    assert reopened.list_intent_hierarchy_nodes(level=IntentLevel.STRATEGIC_OBJECTIVE) == [strategic]
    assert reopened.list_intent_hierarchy_nodes(level="programme") == [programme]
    assert reopened.list_intent_hierarchy_nodes(level=IntentLevel.ENGINEERING_OBJECTIVE) == [eng_1, eng_2]
    assert reopened.list_intent_hierarchy_nodes(parent_id="prog-1") == [eng_1]
    assert reopened.list_intent_hierarchy_nodes(
        level="engineering_objective", parent_id="prog-other"
    ) == [eng_2]

    updated_eo = eng_1.model_copy(
        update={
            "decomposition_status": DecompositionStatus.VERIFIED,
            "cross_eip_proof_passed": True,
        }
    )
    reopened.save_intent_hierarchy_node(updated_eo)
    assert ServerStore(db_path).load_intent_hierarchy_node("eo-1") == updated_eo


def test_domain_proposal_record_crud_and_filtering(tmp_path):
    db_path = tmp_path / "domain_proposals.sqlite3"
    store = ServerStore(db_path)

    with pytest.raises(KeyError):
        store.load_domain_proposal_record("missing-prop")

    prop_1 = StoredDomainProposalRecord(
        proposal_id="prop-k8s-1",
        run_id="run-dom-1",
        domain="kubernetes",
        target_resource="deployment/api-gateway",
        payload_digest="d" * 64,
        decision_id="dec-1",
        can_release=True,
        applied=False,
        proposal_payload={"replicas": 3, "namespace": "prod"},
        created_at="2026-07-15T11:00:00+00:00",
    )
    prop_2 = StoredDomainProposalRecord(
        proposal_id="prop-tf-1",
        run_id="run-dom-1",
        domain="terraform",
        target_resource="aws_s3_bucket.evidence",
        payload_digest="e" * 64,
        decision_id=None,
        can_release=False,
        applied=False,
        proposal_payload={"acl": "private"},
        created_at="2026-07-15T11:05:00+00:00",
    )
    prop_3 = StoredDomainProposalRecord(
        proposal_id="prop-k8s-2",
        run_id="run-dom-2",
        domain="kubernetes",
        target_resource="service/worker",
        payload_digest="f" * 64,
        decision_id="dec-2",
        can_release=True,
        applied=True,
        proposal_payload={"port": 8443},
        apply_receipt={"revision": 7},
        created_at="2026-07-15T11:10:00+00:00",
    )

    store.save_domain_proposal_record(prop_1)
    store.save_domain_proposal_record(prop_2)
    store.save_domain_proposal_record(prop_3)

    reopened = ServerStore(db_path)
    assert reopened.load_domain_proposal_record("prop-k8s-1") == prop_1
    assert reopened.load_domain_proposal_record("prop-k8s-2") == prop_3
    assert reopened.list_domain_proposal_records() == [prop_1, prop_2, prop_3]
    assert reopened.list_domain_proposal_records(run_id="run-dom-1") == [prop_1, prop_2]
    assert reopened.list_domain_proposal_records(domain="kubernetes") == [prop_1, prop_3]
    assert reopened.list_domain_proposal_records(run_id="run-dom-1", domain="terraform") == [prop_2]

    applied_prop_1 = prop_1.model_copy(
        update={
            "applied": True,
            "apply_receipt": {"applied_by": "operator-1", "revision": 2},
        }
    )
    reopened.save_domain_proposal_record(applied_prop_1)
    assert ServerStore(db_path).load_domain_proposal_record("prop-k8s-1") == applied_prop_1


def test_remote_worker_and_artifact_transfer_manifest_crud(tmp_path):
    db_path = tmp_path / "remote_workers.sqlite3"
    store = ServerStore(db_path)

    with pytest.raises(KeyError):
        store.load_remote_worker("missing-worker")
    with pytest.raises(KeyError):
        store.load_artifact_transfer_manifest("missing-transfer")

    builder_worker = RemoteWorkerIdentity(
        worker_id="worker-builder-1",
        host_type="kubernetes_pod",
        role=HostRole.BUILDER,
        auth_method=WorkerAuthMethod.MTLS,
        identity_subject="spiffe://vad.local/ns/runners/sa/builder-1",
        endpoint_url="https://127.0.0.1:8443/worker",
        cert_or_issuer_ref="cert://ca-bundle/builder-pool",
        allowed_risk_tiers=[RiskTier.LOW, RiskTier.MEDIUM],
    )
    verifier_worker = RemoteWorkerIdentity(
        worker_id="worker-verifier-1",
        host_type="vm_runner",
        role=HostRole.VERIFIER,
        auth_method=WorkerAuthMethod.OIDC,
        identity_subject="https://issuer.local/workers/verifier-1",
        endpoint_url="https://127.0.0.1:8443/worker",
        cert_or_issuer_ref="oidc://issuer.local/jwks",
        allowed_risk_tiers=[RiskTier.LOW, RiskTier.MEDIUM, RiskTier.HIGH],
    )

    store.save_remote_worker(builder_worker, operator_intent_ref="intent-remote-opt-in-1")
    store.save_remote_worker(verifier_worker, operator_intent_ref="intent-remote-opt-in-2")

    reopened = ServerStore(db_path)
    assert reopened.load_remote_worker("worker-builder-1") == builder_worker
    assert reopened.load_remote_worker("worker-verifier-1") == verifier_worker
    assert reopened.list_remote_workers() == [builder_worker, verifier_worker]
    assert reopened.list_remote_workers(role="builder") == [builder_worker]
    assert reopened.list_remote_workers(role=HostRole.VERIFIER) == [verifier_worker]

    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT operator_intent_ref FROM remote_workers WHERE worker_id = ?",
            ("worker-builder-1",),
        ).fetchone()
    assert row[0] == "intent-remote-opt-in-1"

    manifest_1 = ArtifactTransferManifest(
        transfer_id="xfer-1",
        run_id="run-xfer-1",
        work_item_id="wi-build-1",
        fence_token=1,
        artifacts={
            "evidence/verification.json": "a" * 64,
            "logs/pytest.txt": "b" * 64,
        },
    )
    manifest_2 = ArtifactTransferManifest(
        transfer_id="xfer-2",
        run_id="run-xfer-1",
        work_item_id="wi-verify-1",
        fence_token=2,
        artifacts={"reports/attestation.json": "c" * 64},
    )
    manifest_3 = ArtifactTransferManifest(
        transfer_id="xfer-3",
        run_id="run-xfer-2",
        work_item_id="wi-build-1",
        fence_token=1,
        artifacts={"patch/diff.patch": "d" * 64},
    )

    reopened.save_artifact_transfer_manifest(manifest_1)
    reopened.save_artifact_transfer_manifest(manifest_2)
    reopened.save_artifact_transfer_manifest(manifest_3)

    reopened_2 = ServerStore(db_path)
    assert reopened_2.load_artifact_transfer_manifest("xfer-1") == manifest_1
    assert reopened_2.list_artifact_transfer_manifests() == [manifest_1, manifest_2, manifest_3]
    assert reopened_2.list_artifact_transfer_manifests(run_id="run-xfer-1") == [manifest_1, manifest_2]
    assert reopened_2.list_artifact_transfer_manifests(work_item_id="wi-build-1") == [manifest_1, manifest_3]
    assert reopened_2.list_artifact_transfer_manifests(
        run_id="run-xfer-1", work_item_id="wi-verify-1"
    ) == [manifest_2]
