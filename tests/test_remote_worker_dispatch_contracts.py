from __future__ import annotations

import hashlib

import pytest
from pydantic import ValidationError

from vad.contracts.models import RiskTier
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
from vad.control_plane.sessions import HostRole


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


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
        allowed_risk_tiers=allowed_risk_tiers or [RiskTier.LOW, RiskTier.MEDIUM],
    )


def _make_manifest(
    *,
    transfer_id: str = "xfer-001",
    run_id: str = "run-100",
    work_item_id: str = "wi-42",
    fence_token: int = 3,
    payloads: dict[str, bytes] | None = None,
) -> tuple[ArtifactTransferManifest, dict[str, bytes]]:
    raw_payloads = payloads if payloads is not None else {
        "inputs/patch.diff": b"--- a/app.py\n+++ b/app.py\n",
        "inputs/context.json": b'{"risk":"medium"}\n',
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
    eip_digest: str | None = None,
    proof_plan_digest: str | None = None,
    risk_tier: RiskTier = RiskTier.MEDIUM,
    required_role: HostRole = HostRole.VERIFIER,
    fence_token: int = 3,
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
        eip_digest=eip_digest or _sha256_hex(b"eip-spec-v1"),
        proof_plan_digest=proof_plan_digest or _sha256_hex(b"proof-plan-v1"),
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
    fence_token: int = 3,
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


def test_verify_bundle_accepts_valid_and_rejects_missing_extra_and_tampered() -> None:
    manifest, payloads = _make_manifest()

    ok, failures = manifest.verify_bundle(payloads)
    assert ok is True
    assert failures == []

    missing_ok, missing_failures = manifest.verify_bundle({"inputs/patch.diff": payloads["inputs/patch.diff"]})
    assert missing_ok is False
    assert any("inputs/context.json" in item and "missing" in item for item in missing_failures)

    extra_payloads = {**payloads, "inputs/unlisted.txt": b"surprise"}
    extra_ok, extra_failures = manifest.verify_bundle(extra_payloads)
    assert extra_ok is False
    assert any("inputs/unlisted.txt" in item for item in extra_failures)

    extra_allowed_ok, extra_allowed_failures = manifest.verify_bundle(extra_payloads, allow_extra_files=True)
    assert extra_allowed_ok is True
    assert extra_allowed_failures == []

    tampered_payloads = {**payloads, "inputs/context.json": b'{"risk":"tampered"}\n'}
    tampered_ok, tampered_failures = manifest.verify_bundle(tampered_payloads)
    assert tampered_ok is False
    assert any("inputs/context.json" in item and "digest" in item for item in tampered_failures)


def test_remote_dispatch_envelope_validation_and_manifest_binding() -> None:
    valid_envelope = _make_envelope()
    assert valid_envelope.fence_token == 3

    for bad_id in ("", "   ", "bad/id", "bad\\id", "bad..id", "bad\nid"):
        with pytest.raises(ValidationError):
            _make_envelope(dispatch_id=bad_id)
        with pytest.raises(ValidationError):
            _make_envelope(worker_id=bad_id)

    for bad_digest in ("abc", "A" * 64, "z" * 64, _sha256_hex(b"x")[:-1]):
        with pytest.raises(ValidationError):
            _make_envelope(eip_digest=bad_digest)
        with pytest.raises(ValidationError):
            _make_envelope(proof_plan_digest=bad_digest)

    for bad_intent in ("", "   ", "intent\nline", "token=raw-secret", "sk-1234567890abcdef1234"):
        with pytest.raises(ValidationError):
            _make_envelope(operator_intent_ref=bad_intent)

    mismatched_run_manifest, _ = _make_manifest(run_id="run-other", work_item_id="wi-42", fence_token=3)
    with pytest.raises(ValidationError, match="run_id"):
        _make_envelope(input_manifest=mismatched_run_manifest)

    mismatched_wi_manifest, _ = _make_manifest(run_id="run-100", work_item_id="wi-other", fence_token=3)
    with pytest.raises(ValidationError, match="work_item_id"):
        _make_envelope(input_manifest=mismatched_wi_manifest)

    mismatched_fence_manifest, _ = _make_manifest(run_id="run-100", work_item_id="wi-42", fence_token=2)
    with pytest.raises(ValidationError, match="fence_token"):
        _make_envelope(input_manifest=mismatched_fence_manifest)


def test_remote_execution_receipt_validation_and_manifest_binding() -> None:
    valid_receipt = _make_receipt()
    assert valid_receipt.passed is True

    for bad_id in ("", "   ", "bad/id", "bad..id", "bad\tid"):
        with pytest.raises(ValidationError):
            _make_receipt(receipt_id=bad_id)
        with pytest.raises(ValidationError):
            _make_receipt(actor=bad_id)

    with pytest.raises(ValidationError):
        _make_receipt(summary="")

    mismatched_run_manifest, _ = _make_manifest(
        transfer_id="xfer-out-002",
        run_id="run-wrong",
        work_item_id="wi-42",
        fence_token=3,
    )
    with pytest.raises(ValidationError, match="run_id"):
        _make_receipt(output_manifest=mismatched_run_manifest)

    mismatched_wi_manifest, _ = _make_manifest(
        transfer_id="xfer-out-003",
        run_id="run-100",
        work_item_id="wi-wrong",
        fence_token=3,
    )
    with pytest.raises(ValidationError, match="work_item_id"):
        _make_receipt(output_manifest=mismatched_wi_manifest)

    mismatched_fence_manifest, _ = _make_manifest(
        transfer_id="xfer-out-004",
        run_id="run-100",
        work_item_id="wi-42",
        fence_token=99,
    )
    with pytest.raises(ValidationError, match="fence_token"):
        _make_receipt(output_manifest=mismatched_fence_manifest)


def test_authorize_dispatch_enforces_local_first_role_risk_fence_and_separation() -> None:
    worker = _make_worker(
        worker_id="verifier-01",
        role=HostRole.VERIFIER,
        endpoint_url="https://localhost:8443/verifier",
        allowed_risk_tiers=[RiskTier.LOW, RiskTier.MEDIUM],
    )
    envelope = _make_envelope(
        worker_id="verifier-01",
        risk_tier=RiskTier.MEDIUM,
        required_role=HostRole.VERIFIER,
        fence_token=3,
    )

    local_default_controller = RemoteWorkerAdmissionController()
    ok_local, reasons_local = local_default_controller.authorize_dispatch(worker, envelope)
    assert ok_local is False
    assert any("local-first" in r or "disabled" in r for r in reasons_local)

    opt_in_controller = RemoteWorkerAdmissionController(
        RemoteWorkerPoolPolicy(
            mode=WorkerExecutionMode.OPERATOR_HOSTED_REMOTE,
            remote_workers_enabled=True,
        )
    )

    ok, reasons = opt_in_controller.authorize_dispatch(
        worker,
        envelope,
        active_fence_token=3,
        builder_actor="codex-builder-01",
    )
    assert ok is True
    assert reasons == []

    # Worker ID mismatch
    wrong_worker_env = _make_envelope(worker_id="verifier-99")
    ok_wid, reasons_wid = opt_in_controller.authorize_dispatch(worker, wrong_worker_env)
    assert ok_wid is False
    assert any("worker_id" in r for r in reasons_wid)

    # Role mismatch
    builder_env = _make_envelope(required_role=HostRole.BUILDER)
    ok_role, reasons_role = opt_in_controller.authorize_dispatch(worker, builder_env)
    assert ok_role is False
    assert any("role" in r for r in reasons_role)

    # Disallowed risk tier
    high_risk_env = _make_envelope(risk_tier=RiskTier.HIGH)
    ok_risk, reasons_risk = opt_in_controller.authorize_dispatch(worker, high_risk_env)
    assert ok_risk is False
    assert any("risk_tier" in r for r in reasons_risk)

    # Stale fence token
    ok_stale, reasons_stale = opt_in_controller.authorize_dispatch(
        worker,
        envelope,
        active_fence_token=4,
    )
    assert ok_stale is False
    assert any("fence_token" in r for r in reasons_stale)

    # Builder/verifier separation of duties: matching worker_id
    ok_sep_wid, reasons_sep_wid = opt_in_controller.authorize_dispatch(
        worker,
        envelope,
        builder_actor=" verifier-01 ",
    )
    assert ok_sep_wid is False
    assert any("separation of duties" in r for r in reasons_sep_wid)

    # Builder/verifier separation of duties: matching identity_subject
    ok_sep_sub, reasons_sep_sub = opt_in_controller.authorize_dispatch(
        worker,
        envelope,
        builder_actor=worker.identity_subject,
    )
    assert ok_sep_sub is False
    assert any("separation of duties" in r for r in reasons_sep_sub)


def test_verify_execution_receipt_checks_binding_fence_and_output_bundle() -> None:
    controller = RemoteWorkerAdmissionController(
        RemoteWorkerPoolPolicy(
            mode=WorkerExecutionMode.OPERATOR_HOSTED_REMOTE,
            remote_workers_enabled=True,
        )
    )
    worker = _make_worker(endpoint_url="http://127.0.0.1:8081/verifier")
    envelope = _make_envelope(fence_token=5)
    out_manifest, out_payloads = _make_manifest(
        transfer_id="xfer-out-005",
        run_id=envelope.run_id,
        work_item_id=envelope.work_item_id,
        fence_token=5,
        payloads={"reports/result.json": b'{"status":"passed"}\n'},
    )
    receipt = _make_receipt(
        dispatch_id=envelope.dispatch_id,
        worker_id=worker.worker_id,
        run_id=envelope.run_id,
        work_item_id=envelope.work_item_id,
        fence_token=5,
        role=HostRole.VERIFIER,
        output_manifest=out_manifest,
    )

    ok, reasons = controller.verify_execution_receipt(
        worker,
        envelope,
        receipt,
        output_payloads=out_payloads,
        active_fence_token=5,
    )
    assert ok is True
    assert reasons == []

    # Dispatch, worker, run, work_item, role mismatches
    bad_dispatch = _make_receipt(dispatch_id="disp-other", fence_token=5, output_manifest=out_manifest)
    assert controller.verify_execution_receipt(worker, envelope, bad_dispatch)[0] is False

    bad_worker = _make_receipt(worker_id="verifier-other", fence_token=5, output_manifest=out_manifest)
    assert controller.verify_execution_receipt(worker, envelope, bad_worker)[0] is False

    bad_run_manifest, _ = _make_manifest(transfer_id="xfer-out-r", run_id="run-other", fence_token=5)
    bad_run = _make_receipt(run_id="run-other", fence_token=5, output_manifest=bad_run_manifest)
    assert controller.verify_execution_receipt(worker, envelope, bad_run)[0] is False

    bad_wi_manifest, _ = _make_manifest(transfer_id="xfer-out-w", work_item_id="wi-other", fence_token=5)
    bad_wi = _make_receipt(work_item_id="wi-other", fence_token=5, output_manifest=bad_wi_manifest)
    assert controller.verify_execution_receipt(worker, envelope, bad_wi)[0] is False

    bad_role = _make_receipt(role=HostRole.BUILDER, fence_token=5, output_manifest=out_manifest)
    assert controller.verify_execution_receipt(worker, envelope, bad_role)[0] is False

    # Fence token mismatch against envelope and against active_fence_token
    stale_manifest, _ = _make_manifest(transfer_id="xfer-out-f", fence_token=4)
    stale_receipt = _make_receipt(fence_token=4, output_manifest=stale_manifest)
    ok_env_fence, reasons_env_fence = controller.verify_execution_receipt(worker, envelope, stale_receipt)
    assert ok_env_fence is False
    assert any("envelope fence_token" in r for r in reasons_env_fence)

    ok_active_fence, reasons_active_fence = controller.verify_execution_receipt(
        worker,
        envelope,
        receipt,
        active_fence_token=6,
    )
    assert ok_active_fence is False
    assert any("active_fence_token" in r for r in reasons_active_fence)

    # Output payload bundle tampering / extra file
    ok_tampered, reasons_tampered = controller.verify_execution_receipt(
        worker,
        envelope,
        receipt,
        output_payloads={"reports/result.json": b'{"status":"tampered"}\n'},
    )
    assert ok_tampered is False
    assert any("digest mismatch" in r for r in reasons_tampered)
