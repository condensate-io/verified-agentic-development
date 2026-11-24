from __future__ import annotations

import hashlib

import pytest
from pydantic import ValidationError

from vad.contracts.models import RiskTier
from vad.control_plane.remote_workers import (
    ArtifactTransferManifest,
    RemoteWorkerAdmissionController,
    RemoteWorkerIdentity,
    RemoteWorkerPoolPolicy,
    WorkerAuthMethod,
    WorkerExecutionMode,
)
from vad.control_plane.sessions import HostRole


def _make_worker(
    *,
    worker_id: str = "worker-01",
    host_type: str = "verifier_runner",
    role: HostRole = HostRole.VERIFIER,
    auth_method: WorkerAuthMethod = WorkerAuthMethod.MTLS,
    identity_subject: str = "spiffe://vad.local/ns/workers/sa/verifier-01",
    endpoint_url: str = "https://127.0.0.1:8443/worker",
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


def test_local_first_default_denies_remote_worker_admission() -> None:
    default_policy = RemoteWorkerPoolPolicy()
    assert default_policy.mode == WorkerExecutionMode.LOCAL_FIRST
    assert default_policy.remote_workers_enabled is False
    assert default_policy.require_operator_intent_for_remote is True

    controller = RemoteWorkerAdmissionController()
    admitted, reasons = controller.admit_worker(
        _make_worker(),
        operator_intent_ref="intent-local-default-test",
    )
    assert admitted is False
    assert any("local-first" in reason or "disabled" in reason for reason in reasons)

    mode_only_policy = RemoteWorkerPoolPolicy(
        mode=WorkerExecutionMode.OPERATOR_HOSTED_REMOTE,
        remote_workers_enabled=False,
    )
    admitted_mode_only, reasons_mode_only = RemoteWorkerAdmissionController(mode_only_policy).admit_worker(
        _make_worker(),
        operator_intent_ref="intent-mode-only",
    )
    assert admitted_mode_only is False
    assert reasons_mode_only


def test_opt_in_admits_mtls_and_oidc_workers_with_operator_intent() -> None:
    policy = RemoteWorkerPoolPolicy(
        mode=WorkerExecutionMode.OPERATOR_HOSTED_REMOTE,
        remote_workers_enabled=True,
    )
    controller = RemoteWorkerAdmissionController(policy)

    mtls_worker = _make_worker(
        worker_id="mtls-codex-01",
        host_type="codex",
        role=HostRole.BUILDER,
        auth_method=WorkerAuthMethod.MTLS,
        endpoint_url="https://127.0.0.1:8443/codex",
        cert_or_issuer_ref="spiffe://cluster.local/ns/vad/ca/workers",
    )
    oidc_worker = _make_worker(
        worker_id="oidc-verifier-01",
        host_type="verifier_runner",
        role=HostRole.VERIFIER,
        auth_method=WorkerAuthMethod.OIDC,
        identity_subject="oidc:sub:verifier-runner-01",
        endpoint_url="https://localhost:9443/verifier",
        cert_or_issuer_ref="https://localhost:8443/realms/vad",
    )

    admitted_mtls, reasons_mtls = controller.admit_worker(
        mtls_worker,
        operator_intent_ref="intent-opt-in-001",
    )
    admitted_oidc, reasons_oidc = controller.admit_worker(
        oidc_worker,
        operator_intent_ref="intent-opt-in-002",
    )

    assert admitted_mtls is True
    assert reasons_mtls == []
    assert admitted_oidc is True
    assert reasons_oidc == []


def test_operator_intent_requirement_enforced_when_remote_enabled() -> None:
    policy = RemoteWorkerPoolPolicy(
        mode=WorkerExecutionMode.OPERATOR_HOSTED_REMOTE,
        remote_workers_enabled=True,
        require_operator_intent_for_remote=True,
    )
    controller = RemoteWorkerAdmissionController(policy)

    admitted_missing, reasons_missing = controller.admit_worker(_make_worker())
    assert admitted_missing is False
    assert any("operator_intent_ref" in reason for reason in reasons_missing)

    admitted_blank, reasons_blank = controller.admit_worker(
        _make_worker(),
        operator_intent_ref="   ",
    )
    assert admitted_blank is False
    assert any("operator_intent_ref" in reason for reason in reasons_blank)

    relaxed_policy = RemoteWorkerPoolPolicy(
        mode=WorkerExecutionMode.OPERATOR_HOSTED_REMOTE,
        remote_workers_enabled=True,
        require_operator_intent_for_remote=False,
    )
    admitted_relaxed, reasons_relaxed = RemoteWorkerAdmissionController(relaxed_policy).admit_worker(
        _make_worker()
    )
    assert admitted_relaxed is True
    assert reasons_relaxed == []


def test_auth_method_enforcement() -> None:
    mtls_only_policy = RemoteWorkerPoolPolicy(
        mode=WorkerExecutionMode.OPERATOR_HOSTED_REMOTE,
        remote_workers_enabled=True,
        allowed_auth_methods=[WorkerAuthMethod.MTLS],
    )
    controller = RemoteWorkerAdmissionController(mtls_only_policy)

    admitted_mtls, reasons_mtls = controller.admit_worker(
        _make_worker(auth_method=WorkerAuthMethod.MTLS),
        operator_intent_ref="intent-auth-01",
    )
    assert admitted_mtls is True
    assert reasons_mtls == []

    admitted_oidc, reasons_oidc = controller.admit_worker(
        _make_worker(auth_method=WorkerAuthMethod.OIDC),
        operator_intent_ref="intent-auth-02",
    )
    assert admitted_oidc is False
    assert any("oidc" in reason.lower() for reason in reasons_oidc)


def test_endpoint_url_rejects_plaintext_non_local_http_and_allows_local_loopback() -> None:
    for local_url in (
        "https://127.0.0.1:8443/worker",
        "https://localhost:8443/worker",
        "http://127.0.0.1:8081/worker",
        "http://localhost:8081/worker",
    ):
        worker = _make_worker(endpoint_url=local_url)
        assert worker.endpoint_url == local_url

    plaintext_non_local_ip = "http://" + "192.0.2.10:8080/worker"
    plaintext_non_local_host = "http://" + "worker.invalid:8080/worker"
    for invalid_url in (
        plaintext_non_local_ip,
        plaintext_non_local_host,
        "https://user:pass@localhost:8443/worker",
        "ftp://localhost:21/worker",
    ):
        with pytest.raises(ValidationError):
            _make_worker(endpoint_url=invalid_url)


def test_cert_or_issuer_ref_rejects_raw_keys_and_tokens() -> None:
    for bad_ref in (
        "-----BEGIN PRIVATE KEY-----abc",
        "api_key=supersecret",
        "token=raw-secret-token",
        "Bearer eyJhbGciOiJIUzI1NiJ9.payload.sig",
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjMifQ.signature",
    ):
        with pytest.raises(ValidationError):
            _make_worker(cert_or_issuer_ref=bad_ref)


def test_artifact_transfer_manifest_integrity_and_path_traversal_rejection() -> None:
    payload = b'{"status":"passed","suite":"unit"}\n'
    digest = hashlib.sha256(payload).hexdigest()

    manifest = ArtifactTransferManifest(
        transfer_id="xfer-001",
        run_id="run-100",
        work_item_id="wi-42",
        fence_token=3,
        artifacts={"reports/unit.json": digest},
    )

    assert manifest.verify_payload_digest("reports/unit.json", payload) is True
    assert manifest.verify_payload_digest("reports/unit.json", b'{"status":"tampered"}\n') is False
    assert manifest.verify_payload_digest("reports/missing.json", payload) is False

    for unsafe_path in (
        "../secret.txt",
        "reports/../../secret.txt",
        "/etc/passwd",
        "C:/Windows/win.ini",
        "reports\\unit.json",
        "./reports/unit.json",
        "",
    ):
        with pytest.raises(ValidationError):
            ArtifactTransferManifest(
                transfer_id="xfer-bad-path",
                run_id="run-100",
                work_item_id="wi-42",
                fence_token=1,
                artifacts={unsafe_path: digest},
            )

    with pytest.raises(ValidationError):
        ArtifactTransferManifest(
            transfer_id="xfer-bad-digest",
            run_id="run-100",
            work_item_id="wi-42",
            fence_token=1,
            artifacts={"reports/unit.json": "not-a-sha256-digest"},
        )

    with pytest.raises(ValidationError):
        ArtifactTransferManifest(
            transfer_id="xfer-bad-fence",
            run_id="run-100",
            work_item_id="wi-42",
            fence_token=0,
            artifacts={"reports/unit.json": digest},
        )


def test_rbac_release_guardian_group_authorization() -> None:
    unconfigured_controller = RemoteWorkerAdmissionController(
        RemoteWorkerPoolPolicy(
            mode=WorkerExecutionMode.OPERATOR_HOSTED_REMOTE,
            remote_workers_enabled=True,
            rbac_release_guardian_groups=[],
        )
    )
    assert unconfigured_controller.authorize_release_guardian_group(["sec-release-guardians"]) is False

    configured_controller = RemoteWorkerAdmissionController(
        RemoteWorkerPoolPolicy(
            mode=WorkerExecutionMode.OPERATOR_HOSTED_REMOTE,
            remote_workers_enabled=True,
            rbac_release_guardian_groups=["sec-release-guardians", "platform-approvers"],
        )
    )
    assert configured_controller.authorize_release_guardian_group(["devs", "sec-release-guardians"]) is True
    assert configured_controller.authorize_release_guardian_group(["platform-approvers"]) is True
    assert configured_controller.authorize_release_guardian_group(["devs", "builders"]) is False
    assert configured_controller.authorize_release_guardian_group([]) is False
