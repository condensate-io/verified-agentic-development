"""Tests for HorizonApiService control-plane request/response handlers."""

from __future__ import annotations

import hashlib
from pathlib import Path

from vad.server.api.horizon import HorizonApiService
from vad.server.db.store import ServerStore


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _make_store(tmp_path: Path) -> ServerStore:
    store = ServerStore(tmp_path / "horizon.db")
    store.migrate()
    return store


def test_horizon_api_returns_503_when_store_is_none() -> None:
    service = HorizonApiService(store=None)
    expected = (
        503,
        {"error": "service_unavailable", "message": "database not configured"},
    )

    assert service.create_hierarchy_node({"level": "strategic_objective"}) == expected
    assert service.list_hierarchy_nodes() == expected
    assert service.approve_objective("obj-1", {"approver": "guardian-1"}) == expected
    assert service.get_objective_rollup("obj-1") == expected
    assert service.list_domain_packs() == expected
    assert service.evaluate_domain_pack_proposal({}) == expected
    assert service.list_remote_workers() == expected
    assert service.admit_remote_worker({}) == expected
    assert service.record_transfer_manifest({}) == expected
    assert service.list_transfer_manifests() == expected


def test_hierarchy_node_lifecycle_approval_and_rollup(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    service = HorizonApiService(store=store)

    # 1. Create StrategicObjective (201)
    status, strategic_res = service.create_hierarchy_node(
        {
            "node_id": "strat-01",
            "level": "strategic_objective",
            "title": "Zero-Trust Control Plane",
            "description": "Enforce strict verification across all changes",
            "risk_floor": "medium",
            "assurance_floor": "A2_INDEPENDENT",
            "inherited_invariants": ["no-self-approval", "signed-dsse"],
            "max_total_cost_usd": 50.0,
        }
    )
    assert status == 201
    assert strategic_res["node"]["node_id"] == "strat-01"
    assert strategic_res["node"]["level"] == "strategic_objective"

    # 2. Floor downgrade rejection on Programme (422)
    status, err_res = service.create_hierarchy_node(
        {
            "node_id": "prog-bad",
            "level": "programme",
            "title": "Downgraded Programme",
            "description": "Attempts to lower risk_floor",
            "parent_id": "strat-01",
            "risk_floor": "low",
            "assurance_floor": "A2_INDEPENDENT",
            "inherited_invariants": ["no-self-approval", "signed-dsse"],
            "max_total_cost_usd": 25.0,
        }
    )
    assert status == 422
    assert err_res["error"] == "invalid_hierarchy_node"
    assert "downgrades" in err_res["detail"]

    # 3. Create valid Programme (201)
    status, prog_res = service.create_hierarchy_node(
        {
            "node_id": "prog-01",
            "level": "programme",
            "title": "Core Auth Hardening",
            "description": "Harden auth flows",
            "parent_id": "strat-01",
            "risk_floor": "medium",
            "assurance_floor": "A2_INDEPENDENT",
            "inherited_invariants": ["no-self-approval", "signed-dsse"],
            "max_total_cost_usd": 30.0,
        }
    )
    assert status == 201
    assert prog_res["node"]["parent_id"] == "strat-01"

    # 4. Floor downgrade rejection on EngineeringObjective child EIP (422)
    status, child_err = service.create_hierarchy_node(
        {
            "node_id": "obj-bad-child",
            "level": "engineering_objective",
            "title": "Bad Child Assurance",
            "description": "Child EIP downgrades assurance",
            "parent_id": "prog-01",
            "risk_floor": "medium",
            "assurance_floor": "A2_INDEPENDENT",
            "inherited_invariants": ["no-self-approval", "signed-dsse"],
            "max_total_cost_usd": 20.0,
            "child_eips": [
                {
                    "eip_name": "eip-schema",
                    "eip_digest": _sha256_hex(b"schema"),
                    "risk_tier": "medium",
                    "assurance_level": "A1_BASIC",
                    "cost_usd": 5.0,
                }
            ],
        }
    )
    assert status == 422
    assert child_err["error"] == "invalid_hierarchy_node"

    # 5. Create valid EngineeringObjective (201)
    status, obj_res = service.create_hierarchy_node(
        {
            "node_id": "obj-01",
            "level": "engineering_objective",
            "title": "Token Rotation Rollout",
            "description": "Multi-EIP token rotation",
            "parent_id": "prog-01",
            "risk_floor": "medium",
            "assurance_floor": "A2_INDEPENDENT",
            "inherited_invariants": ["no-self-approval", "signed-dsse"],
            "max_total_cost_usd": 20.0,
            "child_eips": [
                {
                    "eip_name": "eip-keys",
                    "eip_digest": _sha256_hex(b"keys"),
                    "risk_tier": "medium",
                    "assurance_level": "A2_INDEPENDENT",
                    "depends_on": [],
                    "cost_usd": 4.5,
                },
                {
                    "eip_name": "eip-verifier",
                    "eip_digest": _sha256_hex(b"verifier"),
                    "risk_tier": "high",
                    "assurance_level": "A3_ATTESTED",
                    "depends_on": ["eip-keys"],
                    "cost_usd": 5.5,
                },
            ],
        }
    )
    assert status == 201
    assert obj_res["node"]["node_id"] == "obj-01"

    # 6. List nodes with and without filters (200)
    status, all_nodes = service.list_hierarchy_nodes()
    assert status == 200
    assert {n["node_id"] for n in all_nodes["nodes"]} == {"strat-01", "prog-01", "obj-01"}

    status, child_nodes = service.list_hierarchy_nodes(level="programme", parent_id="strat-01")
    assert status == 200
    assert [n["node_id"] for n in child_nodes["nodes"]] == ["prog-01"]

    # 7. Unapproved objective rollup returns schedule=None + release_readiness (200)
    status, draft_rollup = service.get_objective_rollup("obj-01")
    assert status == 200
    assert draft_rollup["schedule"] is None
    assert draft_rollup["release_readiness"]["rollup_verified"] is False
    assert draft_rollup["release_readiness"]["can_release_objective"] is False

    # 8. Builder role cannot approve objective (403)
    status, forbidden_res = service.approve_objective(
        "obj-01",
        {"approver": "agent-builder", "approver_role": "builder"},
    )
    assert status == 403
    assert forbidden_res["error"] == "forbidden"

    # 9. Missing objective returns 404 on approve and rollup
    status, missing_approve = service.approve_objective(
        "obj-missing",
        {"approver": "guardian-1", "approver_role": "release_guardian"},
    )
    assert status == 404
    status, missing_rollup = service.get_objective_rollup("obj-missing")
    assert status == 404

    # 10. Release guardian approval succeeds (200) and enables tranche schedule rollup (200)
    status, approved_res = service.approve_objective(
        "obj-01",
        {"approver": "guardian-alice", "approver_role": "release_guardian"},
    )
    assert status == 200
    assert approved_res["objective"]["decomposition_status"] == "approved"
    assert approved_res["objective"]["approved_by"] == "guardian-alice"

    status, rollup_res = service.get_objective_rollup("obj-01")
    assert status == 200
    assert rollup_res["objective"]["decomposition_status"] == "approved"
    assert rollup_res["schedule"]["tranches"] == [["eip-keys"], ["eip-verifier"]]
    assert rollup_res["schedule"]["ready_to_dispatch"] == ["eip-keys"]
    assert rollup_res["schedule"]["total_cost_usd"] == 10.0
    assert rollup_res["schedule"]["remaining_budget_usd"] == 10.0
    assert rollup_res["release_readiness"]["objective_id"] == "obj-01"


def test_domain_pack_listing_and_proposal_evaluation(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    service = HorizonApiService(store=store)

    status, list_res = service.list_domain_packs()
    assert status == 200
    assert list_res["domains"] == [
        "infrastructure",
        "operations",
        "business",
        "compliance",
    ]
    assert list_res["proposals"] == []

    # Valid Scope B proposal (200)
    status, eval_res = service.evaluate_domain_pack_proposal(
        {
            "run_id": "run-infra-001",
            "domain": "infrastructure",
            "target_resource": "aws_security_group.staging_api",
            "summary": "Allow internal staging subnet ingress",
            "payload": {"environment": "staging", "cidr_blocks": ["10.20.0.0/16"]},
            "rollback_plan": "terraform apply -var-file=previous.tfvars",
            "builder_actor": "infra-builder-1",
            "verifier_actor": "infra-verifier-1",
        }
    )
    assert status == 200
    assert eval_res["run_id"] == "run-infra-001"
    assert eval_res["domain"] == "infrastructure"
    assert eval_res["release_decision"]["can_release"] is True
    assert eval_res["applied"] is True
    assert eval_res["apply_receipt"]["status"] == "applied"

    # Persisted domain proposal is returned on subsequent listing
    status, after_list = service.list_domain_packs()
    assert status == 200
    assert len(after_list["proposals"]) == 1
    assert after_list["proposals"][0]["run_id"] == "run-infra-001"
    assert after_list["proposals"][0]["applied"] is True

    # Separation-of-duties violation: builder == verifier (403)
    status, sod_err = service.evaluate_domain_pack_proposal(
        {
            "run_id": "run-infra-002",
            "domain": "operations",
            "target_resource": "k8s/deployment/read-api",
            "summary": "Scale dev read replicas",
            "payload": {"environment": "dev", "action_type": "scale_read_replicas"},
            "rollback_plan": "kubectl scale deployment/read-api --replicas=2",
            "builder_actor": "same-actor",
            "verifier_actor": "same-actor",
        }
    )
    assert status == 403
    assert sod_err["error"] == "forbidden"

    # Invalid/unregistered domain request (422)
    status, invalid_err = service.evaluate_domain_pack_proposal(
        {
            "run_id": "run-invalid",
            "domain": "software_delivery",
            "target_resource": "repo/app",
            "summary": "Not a Scope B pack",
        }
    )
    assert status == 422
    assert invalid_err["error"] == "invalid_domain_proposal"


def test_remote_worker_admission_listing_and_transfer_manifests(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    service = HorizonApiService(store=store)

    worker_payload = {
        "worker_id": "remote-verifier-01",
        "host_type": "verifier_runner",
        "role": "verifier",
        "auth_method": "mtls",
        "identity_subject": "spiffe://vad.local/ns/workers/sa/remote-verifier-01",
        "endpoint_url": "https://127.0.0.1:8443/verifier",
        "cert_or_issuer_ref": "vault://pki/vad/remote-verifier-01",
        "allowed_risk_tiers": ["low", "medium", "high"],
    }

    # 1. Fail-closed under default LOCAL_FIRST policy (403)
    status, denied_res = service.admit_remote_worker(
        {
            "worker": worker_payload,
            "operator_intent_ref": "op-intent-remote-001",
        }
    )
    assert status == 403
    assert denied_res["admitted"] is False
    assert denied_res["worker"] is None
    assert any("local-first default" in reason for reason in denied_res["reasons"])

    # 2. Opt in via OPERATOR_HOSTED_REMOTE policy + operator_intent_ref (201)
    status, admitted_res = service.admit_remote_worker(
        {
            "policy": {
                "mode": "operator_hosted_remote",
                "remote_workers_enabled": True,
                "require_operator_intent_for_remote": True,
                "allowed_auth_methods": ["mtls", "oidc"],
                "rbac_release_guardian_groups": ["release-guardians"],
            },
            "worker": worker_payload,
            "operator_intent_ref": "op-intent-remote-001",
        }
    )
    assert status == 201
    assert admitted_res["admitted"] is True
    assert admitted_res["reasons"] == []
    assert admitted_res["worker"]["worker_id"] == "remote-verifier-01"

    # 3. Also support top-level worker fields for subsequent registration (201)
    status, top_level_res = service.admit_remote_worker(
        {
            "worker_id": "remote-builder-01",
            "host_type": "builder_runner",
            "role": "builder",
            "auth_method": "oidc",
            "identity_subject": "spiffe://vad.local/ns/workers/sa/remote-builder-01",
            "endpoint_url": "https://localhost:8444/builder",
            "cert_or_issuer_ref": "oidc://localhost:9443/issuer",
            "allowed_risk_tiers": ["low"],
            "operator_intent_ref": "op-intent-remote-002",
        }
    )
    assert status == 201
    assert top_level_res["admitted"] is True
    assert top_level_res["worker"]["worker_id"] == "remote-builder-01"

    # 4. List remote workers with and without role filter (200)
    status, all_workers = service.list_remote_workers()
    assert status == 200
    assert all_workers["policy"]["mode"] == "operator_hosted_remote"
    assert all_workers["policy"]["remote_workers_enabled"] is True
    assert {w["worker_id"] for w in all_workers["workers"]} == {
        "remote-verifier-01",
        "remote-builder-01",
    }

    status, verifiers_only = service.list_remote_workers(role="verifier")
    assert status == 200
    assert [w["worker_id"] for w in verifiers_only["workers"]] == ["remote-verifier-01"]

    # 5. Record and list ArtifactTransferManifests (201 / 200)
    manifest_1 = {
        "transfer_id": "xfer-001",
        "run_id": "run-remote-1",
        "work_item_id": "wi-1",
        "fence_token": 1,
        "artifacts": {
            "outputs/report.json": _sha256_hex(b'{"passed":true}'),
        },
    }
    manifest_2 = {
        "transfer_id": "xfer-002",
        "run_id": "run-remote-2",
        "work_item_id": "wi-2",
        "fence_token": 2,
        "artifacts": {
            "outputs/trace.log": _sha256_hex(b"ok"),
        },
    }

    status, rec1 = service.record_transfer_manifest(manifest_1)
    assert status == 201
    assert rec1["manifest"]["transfer_id"] == "xfer-001"

    status, rec2 = service.record_transfer_manifest(manifest_2)
    assert status == 201
    assert rec2["manifest"]["transfer_id"] == "xfer-002"

    status, bad_manifest = service.record_transfer_manifest(
        {
            "transfer_id": "xfer-bad",
            "run_id": "run-remote-1",
            "work_item_id": "wi-1",
            "fence_token": 1,
            "artifacts": {"../escape.txt": _sha256_hex(b"bad")},
        }
    )
    assert status == 422
    assert bad_manifest["error"] == "invalid_transfer_manifest"

    status, listed_all = service.list_transfer_manifests()
    assert status == 200
    assert [m["transfer_id"] for m in listed_all["manifests"]] == ["xfer-001", "xfer-002"]

    status, listed_filtered = service.list_transfer_manifests(
        run_id="run-remote-1",
        work_item_id="wi-1",
    )
    assert status == 200
    assert [m["transfer_id"] for m in listed_filtered["manifests"]] == ["xfer-001"]
