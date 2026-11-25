from __future__ import annotations

import hashlib

import pytest

from vad.contracts.models import RiskTier
from vad.control_plane.remote_worker_coordinator import (
    RBACReleaseAuthorizationResult,
    RemoteDispatchSession,
    RemoteReceiptVerificationResult,
    RemoteWorkerCoordinator,
)
from vad.control_plane.remote_workers import (
    ArtifactTransferManifest,
    RemoteDispatchEnvelope,
    RemoteExecutionReceipt,
    RemoteWorkerIdentity,
    RemoteWorkerPoolPolicy,
    WorkerAuthMethod,
    WorkerExecutionMode,
)
from vad.control_plane.sessions import HostRole, HostSessionManager


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class _FakeRemoteStore:
    def __init__(self) -> None:
        self.workers: dict[str, tuple[RemoteWorkerIdentity, str | None]] = {}
        self.manifests: dict[str, ArtifactTransferManifest] = {}

    def save_remote_worker(
        self,
        worker: RemoteWorkerIdentity,
        *,
        operator_intent_ref: str | None = None,
    ) -> RemoteWorkerIdentity:
        self.workers[worker.worker_id] = (worker, operator_intent_ref)
        return worker

    def load_remote_worker(self, worker_id: str) -> RemoteWorkerIdentity:
        if worker_id not in self.workers:
            raise KeyError(worker_id)
        return self.workers[worker_id][0]

    def list_remote_workers(self) -> list[RemoteWorkerIdentity]:
        return [worker for worker, _ in self.workers.values()]

    def save_artifact_transfer_manifest(
        self,
        manifest: ArtifactTransferManifest,
    ) -> ArtifactTransferManifest:
        self.manifests[manifest.transfer_id] = manifest
        return manifest


def _make_worker(
    *,
    worker_id: str = "verifier-01",
    host_type: str = "verifier_runner",
    role: HostRole = HostRole.VERIFIER,
    auth_method: WorkerAuthMethod = WorkerAuthMethod.MTLS,
    identity_subject: str = "spiffe://vad.local/ns/workers/sa/verifier-01",
    endpoint_url: str = "https://127.0.0.1:8443/verifier",
    cert_or_issuer_ref: str = "vault://pki/vad/verifier-01-cert",
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
        allowed_risk_tiers=allowed_risk_tiers or [RiskTier.LOW, RiskTier.MEDIUM, RiskTier.HIGH],
    )


def _make_manifest(
    *,
    transfer_id: str = "xfer-in-001",
    run_id: str = "run-100",
    work_item_id: str = "wi-42",
    fence_token: int = 1,
    payloads: dict[str, bytes] | None = None,
) -> tuple[ArtifactTransferManifest, dict[str, bytes]]:
    raw_payloads = payloads if payloads is not None else {
        "inputs/patch.diff": b"--- a/app.py\n+++ b/app.py\n",
        "inputs/context.json": b'{"risk":"high"}\n',
    }
    manifest = ArtifactTransferManifest(
        transfer_id=transfer_id,
        run_id=run_id,
        work_item_id=work_item_id,
        fence_token=fence_token,
        artifacts={path: _sha256_hex(content) for path, content in raw_payloads.items()},
    )
    return manifest, raw_payloads


def _make_envelope(
    *,
    dispatch_id: str = "disp-001",
    worker_id: str = "verifier-01",
    run_id: str = "run-100",
    work_item_id: str = "wi-42",
    risk_tier: RiskTier = RiskTier.HIGH,
    required_role: HostRole = HostRole.VERIFIER,
    fence_token: int = 1,
    operator_intent_ref: str = "intent-dispatch-001",
    input_manifest: ArtifactTransferManifest | None = None,
) -> RemoteDispatchEnvelope:
    manifest = input_manifest or _make_manifest(
        run_id=run_id,
        work_item_id=work_item_id,
        fence_token=fence_token,
    )[0]
    return RemoteDispatchEnvelope(
        dispatch_id=dispatch_id,
        worker_id=worker_id,
        run_id=run_id,
        work_item_id=work_item_id,
        eip_digest=_sha256_hex(b"eip-spec-v1"),
        proof_plan_digest=_sha256_hex(b"proof-plan-v1"),
        risk_tier=risk_tier,
        required_role=required_role,
        fence_token=fence_token,
        operator_intent_ref=operator_intent_ref,
        input_manifest=manifest,
    )


def _make_receipt(
    *,
    receipt_id: str = "rcpt-001",
    dispatch_id: str = "disp-001",
    worker_id: str = "verifier-01",
    run_id: str = "run-100",
    work_item_id: str = "wi-42",
    fence_token: int = 1,
    actor: str = "verifier-01",
    role: HostRole = HostRole.VERIFIER,
    passed: bool = True,
    summary: str = "All verification obligations passed",
    output_manifest: ArtifactTransferManifest | None = None,
) -> RemoteExecutionReceipt:
    manifest = output_manifest or _make_manifest(
        transfer_id="xfer-out-001",
        run_id=run_id,
        work_item_id=work_item_id,
        fence_token=fence_token,
        payloads={"reports/verification.json": b'{"passed":true}\n'},
    )[0]
    return RemoteExecutionReceipt(
        receipt_id=receipt_id,
        dispatch_id=dispatch_id,
        worker_id=worker_id,
        run_id=run_id,
        work_item_id=work_item_id,
        fence_token=fence_token,
        actor=actor,
        role=role,
        passed=passed,
        summary=summary,
        output_manifest=manifest,
    )


def _opt_in_policy(
    *,
    rbac_groups: list[str] | None = None,
) -> RemoteWorkerPoolPolicy:
    return RemoteWorkerPoolPolicy(
        mode=WorkerExecutionMode.OPERATOR_HOSTED_REMOTE,
        remote_workers_enabled=True,
        require_operator_intent_for_remote=True,
        rbac_release_guardian_groups=rbac_groups or ["release-guardians", "sec-approvers"],
    )


def test_local_first_default_rejects_worker_registration_and_dispatch() -> None:
    coordinator = RemoteWorkerCoordinator()
    assert coordinator.policy.mode == WorkerExecutionMode.LOCAL_FIRST
    assert coordinator.policy.remote_workers_enabled is False

    worker = _make_worker(endpoint_url="https://localhost:8443/verifier")
    admitted, reasons = coordinator.register_worker(worker, operator_intent_ref="intent-001")
    assert admitted is False
    assert any("local-first" in reason or "disabled" in reason for reason in reasons)
    assert coordinator.list_workers() == []

    with pytest.raises(KeyError):
        coordinator.get_worker(worker.worker_id)

    envelope = _make_envelope(worker_id=worker.worker_id)
    with pytest.raises(PermissionError, match="not registered"):
        coordinator.dispatch_work(envelope)

    # Even if a worker record is already in a backing store, LOCAL_FIRST must block dispatch.
    store = _FakeRemoteStore()
    store.save_remote_worker(worker, operator_intent_ref="intent-001")
    hydrated_local_coordinator = RemoteWorkerCoordinator(store=store)
    with pytest.raises(PermissionError, match="disabled"):
        hydrated_local_coordinator.dispatch_work(envelope)


def test_opt_in_remote_registration_dispatch_session_and_receipt_ingestion() -> None:
    store = _FakeRemoteStore()
    session_manager = HostSessionManager(signing_secret=b"test-coordinator-secret-key-0123")
    coordinator = RemoteWorkerCoordinator(
        policy=_opt_in_policy(),
        session_manager=session_manager,
        store=store,
    )

    worker = _make_worker(endpoint_url="http://127.0.0.1:8081/verifier")
    admitted, reasons = coordinator.register_worker(worker, operator_intent_ref="intent-reg-001")
    assert admitted is True
    assert reasons == []
    assert coordinator.get_worker(worker.worker_id) == worker
    assert coordinator.list_workers() == [worker]
    assert worker.worker_id in store.workers
    assert store.workers[worker.worker_id][1] == "intent-reg-001"

    input_manifest, input_payloads = _make_manifest(
        transfer_id="xfer-in-002",
        fence_token=2,
    )
    envelope = _make_envelope(
        dispatch_id="disp-002",
        worker_id=worker.worker_id,
        fence_token=2,
        input_manifest=input_manifest,
    )

    # Tampered input payload must fail before advancing fence or storing manifest.
    with pytest.raises(ValueError, match="digest mismatch"):
        coordinator.dispatch_work(
            envelope,
            input_payloads={**input_payloads, "inputs/context.json": b'{"risk":"tampered"}\n'},
            builder_actor="builder-01",
        )
    assert "xfer-in-002" not in store.manifests

    dispatch_session = coordinator.dispatch_work(
        envelope,
        input_payloads=input_payloads,
        builder_actor="builder-01",
    )
    assert isinstance(dispatch_session, RemoteDispatchSession)
    assert dispatch_session.envelope == envelope
    assert dispatch_session.worker == worker
    assert "xfer-in-002" in store.manifests

    claims = dispatch_session.session_token.claims
    assert claims.client_id == worker.worker_id
    assert claims.host_type == worker.host_type
    assert claims.role == envelope.required_role
    assert claims.actor == worker.worker_id
    assert claims.work_item_id == envelope.work_item_id
    assert claims.fence_token == envelope.fence_token

    token_ok, token_err = session_manager.verify(
        dispatch_session.session_token,
        required_role=HostRole.VERIFIER,
        expected_work_item_id=envelope.work_item_id,
        min_fence_token=2,
    )
    assert token_ok is True
    assert token_err is None

    out_manifest, out_payloads = _make_manifest(
        transfer_id="xfer-out-002",
        run_id=envelope.run_id,
        work_item_id=envelope.work_item_id,
        fence_token=2,
        payloads={"reports/verification.json": b'{"passed":true}\n'},
    )
    receipt = _make_receipt(
        receipt_id="rcpt-002",
        dispatch_id=envelope.dispatch_id,
        worker_id=worker.worker_id,
        run_id=envelope.run_id,
        work_item_id=envelope.work_item_id,
        fence_token=2,
        output_manifest=out_manifest,
    )
    verification = coordinator.ingest_execution_receipt(receipt, output_payloads=out_payloads)
    assert isinstance(verification, RemoteReceiptVerificationResult)
    assert verification.verified is True
    assert verification.receipt_id == "rcpt-002"
    assert verification.dispatch_id == "disp-002"
    assert verification.worker_id == worker.worker_id
    assert verification.fence_token == 2
    assert verification.reasons == []
    assert "xfer-out-002" in store.manifests


def test_stale_fence_token_and_tampered_output_payload_rejection() -> None:
    store = _FakeRemoteStore()
    coordinator = RemoteWorkerCoordinator(policy=_opt_in_policy(), store=store)
    worker = _make_worker(endpoint_url="http://localhost:8082/verifier")
    admitted, _ = coordinator.register_worker(worker, operator_intent_ref="intent-reg-002")
    assert admitted is True

    # Generation 1 dispatch
    in_manifest_1, in_payloads_1 = _make_manifest(transfer_id="xfer-in-f1", fence_token=1)
    env_1 = _make_envelope(
        dispatch_id="disp-f1",
        fence_token=1,
        input_manifest=in_manifest_1,
    )
    coordinator.dispatch_work(env_1, input_payloads=in_payloads_1, builder_actor="builder-01")

    # Generation 2 dispatch supersedes generation 1 for the same work_item_id
    in_manifest_2, in_payloads_2 = _make_manifest(transfer_id="xfer-in-f2", fence_token=2)
    env_2 = _make_envelope(
        dispatch_id="disp-f2",
        fence_token=2,
        input_manifest=in_manifest_2,
    )
    coordinator.dispatch_work(env_2, input_payloads=in_payloads_2, builder_actor="builder-01")

    # Re-dispatching with stale fence_token=1 must fail
    stale_env = _make_envelope(
        dispatch_id="disp-stale",
        fence_token=1,
        input_manifest=in_manifest_1,
    )
    with pytest.raises(PermissionError, match="fence_token"):
        coordinator.dispatch_work(stale_env, input_payloads=in_payloads_1, builder_actor="builder-01")

    # Receipt for generation 1 (fence_token=1) must fail against active_fence_token=2
    stale_out_manifest, stale_out_payloads = _make_manifest(
        transfer_id="xfer-out-f1",
        fence_token=1,
        payloads={"reports/verification.json": b'{"passed":true}\n'},
    )
    stale_receipt = _make_receipt(
        receipt_id="rcpt-f1",
        dispatch_id="disp-f1",
        fence_token=1,
        output_manifest=stale_out_manifest,
    )
    stale_res = coordinator.ingest_execution_receipt(
        stale_receipt,
        output_payloads=stale_out_payloads,
    )
    assert stale_res.verified is False
    assert any("active_fence_token" in reason for reason in stale_res.reasons)
    assert "xfer-out-f1" not in store.manifests

    # Receipt for active generation 2 with tampered output payload must fail and not persist
    out_manifest_2, _ = _make_manifest(
        transfer_id="xfer-out-f2",
        fence_token=2,
        payloads={"reports/verification.json": b'{"passed":true}\n'},
    )
    receipt_2 = _make_receipt(
        receipt_id="rcpt-f2",
        dispatch_id="disp-f2",
        fence_token=2,
        output_manifest=out_manifest_2,
    )
    tampered_res = coordinator.ingest_execution_receipt(
        receipt_2,
        output_payloads={"reports/verification.json": b'{"passed":false}\n'},
    )
    assert tampered_res.verified is False
    assert any("digest mismatch" in reason for reason in tampered_res.reasons)
    assert "xfer-out-f2" not in store.manifests

    # Unknown dispatch_id returns verified=False
    unknown_res = coordinator.ingest_execution_receipt(
        _make_receipt(dispatch_id="disp-unknown", fence_token=2, output_manifest=out_manifest_2)
    )
    assert unknown_res.verified is False
    assert "unknown dispatch_id or worker_id" in unknown_res.reasons


def test_authorize_release_with_rbac_high_critical_group_and_builder_denials() -> None:
    coordinator = RemoteWorkerCoordinator(
        policy=_opt_in_policy(rbac_groups=["release-guardians", "platform-security"])
    )

    # HIGH requires at least 1 authorized RBAC guardian
    high_ok = coordinator.authorize_release_with_rbac(
        actor_groups_by_guardian={"guardian-alice": ["release-guardians"]},
        risk_tier=RiskTier.HIGH,
        builder_actor="builder-bob",
        verifier_actor="verifier-01",
    )
    assert isinstance(high_ok, RBACReleaseAuthorizationResult)
    assert high_ok.authorized is True
    assert high_ok.approved_guardians == ["guardian-alice"]
    assert high_ok.denied_actors == []
    assert high_ok.reasons == []

    # CRITICAL requires at least 2 distinct authorized RBAC guardians
    critical_one = coordinator.authorize_release_with_rbac(
        actor_groups_by_guardian={"guardian-alice": ["release-guardians"]},
        risk_tier=RiskTier.CRITICAL,
        builder_actor="builder-bob",
        verifier_actor="verifier-01",
    )
    assert critical_one.authorized is False
    assert critical_one.approved_guardians == ["guardian-alice"]
    assert any("at least 2" in reason for reason in critical_one.reasons)

    critical_two = coordinator.authorize_release_with_rbac(
        actor_groups_by_guardian={
            "guardian-alice": ["release-guardians"],
            "guardian-carol": ["platform-security"],
        },
        risk_tier=RiskTier.CRITICAL,
        builder_actor="builder-bob",
        verifier_actor="verifier-01",
    )
    assert critical_two.authorized is True
    assert critical_two.approved_guardians == ["guardian-alice", "guardian-carol"]
    assert critical_two.denied_actors == []
    assert critical_two.reasons == []

    # Unauthorized RBAC group must be denied even for LOW/MEDIUM if supplied
    unauthorized_group = coordinator.authorize_release_with_rbac(
        actor_groups_by_guardian={"guardian-dave": ["readonly-viewers"]},
        risk_tier=RiskTier.MEDIUM,
        builder_actor="builder-bob",
        verifier_actor="verifier-01",
    )
    assert unauthorized_group.authorized is False
    assert unauthorized_group.approved_guardians == []
    assert unauthorized_group.denied_actors == ["guardian-dave"]
    assert any("RBAC" in reason for reason in unauthorized_group.reasons)

    # Builder self-approval must be denied even if builder belongs to an authorized RBAC group
    builder_self_approval = coordinator.authorize_release_with_rbac(
        actor_groups_by_guardian={
            "builder-bob": ["release-guardians", "platform-security"],
            "guardian-alice": ["release-guardians"],
        },
        risk_tier=RiskTier.HIGH,
        builder_actor=" builder-bob ",
        verifier_actor="verifier-01",
    )
    assert builder_self_approval.authorized is False
    assert builder_self_approval.approved_guardians == ["guardian-alice"]
    assert builder_self_approval.denied_actors == ["builder-bob"]
    assert any("separation of duties" in reason for reason in builder_self_approval.reasons)

    # LOW/MEDIUM with 0 guardians supplied succeeds
    low_empty = coordinator.authorize_release_with_rbac(
        actor_groups_by_guardian={},
        risk_tier=RiskTier.LOW,
        builder_actor="builder-bob",
        verifier_actor="verifier-01",
    )
    assert low_empty.authorized is True
    assert low_empty.approved_guardians == []
    assert low_empty.denied_actors == []
