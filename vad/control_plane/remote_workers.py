"""Opt-in operator-hosted remote worker and enterprise control contracts.

Preserves VAD's local-first default: remote worker admission fails closed
unless explicitly opted in by policy and operator intent, with zero mandatory
hosted SaaS or external control dependencies.
"""

from __future__ import annotations

import hashlib
import hmac
import re
from enum import Enum
from pathlib import PurePosixPath
from urllib.parse import urlparse

from pydantic import BaseModel, Field, field_validator, model_validator

from vad.contracts.models import RiskTier
from vad.control_plane.sessions import HostRole

LOCAL_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
_SHA256_HEX_RE = re.compile(r"^[a-f0-9]{64}$")
_JWT_RE = re.compile(r"^eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$")
_DRIVE_PREFIX_RE = re.compile(r"^[A-Za-z]:")
_CONTROL_SEPARATORS = ("\n", "\r", "\t", "\0")
_IDENTIFIER_FORBIDDEN = ("/", "\\", "..", *_CONTROL_SEPARATORS)
_SECRET_VALUE_MARKERS = (
    "-----begin",
    "private key",
    "api_key=",
    "token=",
    "password=",
    "secret=",
    "private_key=",
    "bearer ",
    "sk-",
    "ghp_",
)


def _reject_control_separators(value: str, field_name: str) -> str:
    if any(sep in value for sep in _CONTROL_SEPARATORS):
        raise ValueError(f"{field_name} must not contain control separators")
    return value


def _validate_safe_identifier(value: str, field_name: str) -> str:
    stripped = value.strip()
    if not stripped:
        raise ValueError(f"{field_name} must not be empty")
    if any(marker in stripped for marker in _IDENTIFIER_FORBIDDEN):
        raise ValueError(f"{field_name} must not contain path, traversal, or control separators")
    return stripped


def _reject_raw_secret(value: str, field_name: str) -> str:
    lowered = value.lower()
    if any(marker in lowered for marker in _SECRET_VALUE_MARKERS) or _JWT_RE.match(value.strip()):
        raise ValueError(f"{field_name} must be a config or secret reference, never raw key or token material")
    return value


def _validate_sha256_hex(value: str, field_name: str) -> str:
    if not _SHA256_HEX_RE.fullmatch(value):
        raise ValueError(f"{field_name} must be a 64-character lowercase SHA-256 hex digest")
    return value


def _validate_non_empty_ref(value: str, field_name: str) -> str:
    _reject_control_separators(value, field_name)
    stripped = value.strip()
    if not stripped:
        raise ValueError(f"{field_name} must not be empty")
    _reject_raw_secret(stripped, field_name)
    return stripped


def _validate_relative_artifact_path(path: str) -> str:
    if not path or not path.strip():
        raise ValueError("artifact path must not be empty")
    _reject_control_separators(path, "artifact path")
    if "\\" in path or path.startswith("/") or _DRIVE_PREFIX_RE.match(path):
        raise ValueError(f"artifact path must be a relative POSIX path: {path!r}")
    segments = path.split("/")
    if any(segment in {"", ".", ".."} for segment in segments):
        raise ValueError(f"artifact path contains invalid or traversal segment: {path!r}")
    posix_parts = PurePosixPath(path).parts
    if ".." in posix_parts:
        raise ValueError(f"artifact path must not contain path traversal '..': {path!r}")
    return path


class WorkerExecutionMode(str, Enum):
    """Execution mode for VAD worker orchestration."""

    LOCAL_FIRST = "local_first"
    OPERATOR_HOSTED_REMOTE = "operator_hosted_remote"


class WorkerAuthMethod(str, Enum):
    """Supported mutual-authentication methods for operator-hosted remote workers."""

    MTLS = "mtls"
    OIDC = "oidc"


class RemoteWorkerIdentity(BaseModel):
    """Identity and transport contract for an opt-in operator-hosted remote worker."""

    worker_id: str = Field(min_length=1, max_length=160)
    host_type: str = Field(min_length=1, max_length=80)
    role: HostRole
    auth_method: WorkerAuthMethod
    identity_subject: str = Field(min_length=1, max_length=320)
    endpoint_url: str = Field(min_length=1, max_length=512)
    cert_or_issuer_ref: str = Field(min_length=1, max_length=320)
    allowed_risk_tiers: list[RiskTier] = Field(default_factory=lambda: [RiskTier.LOW])

    @field_validator("worker_id")
    @classmethod
    def validate_worker_id(cls, value: str) -> str:
        return _validate_safe_identifier(value, "worker_id")

    @field_validator("host_type")
    @classmethod
    def validate_host_type(cls, value: str) -> str:
        return _validate_safe_identifier(value, "host_type")

    @field_validator("identity_subject")
    @classmethod
    def validate_identity_subject(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("identity_subject must not be empty")
        _reject_control_separators(stripped, "identity_subject")
        _reject_raw_secret(stripped, "identity_subject")
        return stripped

    @field_validator("endpoint_url")
    @classmethod
    def validate_endpoint_url(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("endpoint_url must not be empty")
        _reject_control_separators(stripped, "endpoint_url")
        if any(ch.isspace() for ch in stripped):
            raise ValueError("endpoint_url must not contain whitespace")

        parsed = urlparse(stripped)
        if parsed.username or parsed.password:
            raise ValueError("endpoint_url must not embed credentials")

        scheme = parsed.scheme.lower()
        hostname = (parsed.hostname or "").lower()
        if not hostname:
            raise ValueError("endpoint_url must include a valid hostname")

        if scheme == "https":
            return stripped
        if scheme == "http":
            if hostname not in LOCAL_LOOPBACK_HOSTS:
                raise ValueError(
                    "plaintext http:// endpoint_url is rejected for non-local hosts; use https://"
                )
            return stripped
        raise ValueError("endpoint_url scheme must be https (or http for local loopback contract tests)")

    @field_validator("cert_or_issuer_ref")
    @classmethod
    def validate_cert_or_issuer_ref(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("cert_or_issuer_ref must not be empty")
        _reject_control_separators(stripped, "cert_or_issuer_ref")
        _reject_raw_secret(stripped, "cert_or_issuer_ref")
        return stripped


class ArtifactTransferManifest(BaseModel):
    """Content-addressed manifest for transferring verification/build artifacts."""

    transfer_id: str = Field(min_length=1, max_length=160)
    run_id: str = Field(min_length=1, max_length=160)
    work_item_id: str = Field(min_length=1, max_length=160)
    fence_token: int = Field(ge=1)
    artifacts: dict[str, str]

    @field_validator("transfer_id", "run_id", "work_item_id")
    @classmethod
    def validate_identifiers(cls, value: str, info) -> str:
        return _validate_safe_identifier(value, info.field_name)

    @field_validator("artifacts")
    @classmethod
    def validate_artifacts(cls, value: dict[str, str]) -> dict[str, str]:
        validated: dict[str, str] = {}
        for raw_path, digest in value.items():
            clean_path = _validate_relative_artifact_path(raw_path)
            if not _SHA256_HEX_RE.fullmatch(digest):
                raise ValueError(
                    f"artifact digest for {clean_path!r} must be a 64-character lowercase SHA-256 hex digest"
                )
            validated[clean_path] = digest
        return validated

    def verify_payload_digest(self, path: str, data: bytes) -> bool:
        """Verify payload bytes against the manifest's SHA-256 digest in constant time."""
        expected = self.artifacts.get(path)
        if expected is None:
            return False
        actual = hashlib.sha256(data).hexdigest()
        return hmac.compare_digest(actual, expected)

    def verify_bundle(
        self,
        payloads: dict[str, bytes],
        *,
        allow_extra_files: bool = False,
    ) -> tuple[bool, list[str]]:
        """Verify a complete artifact payload bundle against this manifest."""
        failures: list[str] = []

        for path in sorted(self.artifacts):
            if path not in payloads:
                failures.append(f"missing artifact payload for '{path}'")
            elif not self.verify_payload_digest(path, payloads[path]):
                failures.append(f"digest mismatch for artifact '{path}'")

        if not allow_extra_files:
            for path in sorted(payloads):
                if path not in self.artifacts:
                    failures.append(f"unexpected unlisted artifact payload '{path}'")

        return (len(failures) == 0, failures)


def _validate_manifest_binding(
    manifest: ArtifactTransferManifest,
    *,
    manifest_name: str,
    run_id: str,
    work_item_id: str,
    fence_token: int,
) -> None:
    if manifest.run_id != run_id:
        raise ValueError(f"{manifest_name}.run_id must match run_id")
    if manifest.work_item_id != work_item_id:
        raise ValueError(f"{manifest_name}.work_item_id must match work_item_id")
    if manifest.fence_token != fence_token:
        raise ValueError(f"{manifest_name}.fence_token must match fence_token")


class RemoteDispatchEnvelope(BaseModel):
    """Fenced dispatch contract binding EIP, proof plan, risk, role, and input artifacts."""

    dispatch_id: str = Field(min_length=1, max_length=160)
    worker_id: str = Field(min_length=1, max_length=160)
    run_id: str = Field(min_length=1, max_length=160)
    work_item_id: str = Field(min_length=1, max_length=160)
    eip_digest: str
    proof_plan_digest: str
    risk_tier: RiskTier
    required_role: HostRole
    fence_token: int = Field(ge=1)
    operator_intent_ref: str = Field(min_length=1, max_length=320)
    input_manifest: ArtifactTransferManifest

    @field_validator("dispatch_id", "worker_id", "run_id", "work_item_id")
    @classmethod
    def validate_identifiers(cls, value: str, info) -> str:
        return _validate_safe_identifier(value, info.field_name)

    @field_validator("eip_digest", "proof_plan_digest")
    @classmethod
    def validate_digests(cls, value: str, info) -> str:
        return _validate_sha256_hex(value, info.field_name)

    @field_validator("operator_intent_ref")
    @classmethod
    def validate_operator_intent_ref(cls, value: str) -> str:
        return _validate_non_empty_ref(value, "operator_intent_ref")

    @model_validator(mode="after")
    def validate_input_manifest_binding(self) -> RemoteDispatchEnvelope:
        _validate_manifest_binding(
            self.input_manifest,
            manifest_name="input_manifest",
            run_id=self.run_id,
            work_item_id=self.work_item_id,
            fence_token=self.fence_token,
        )
        return self


class RemoteExecutionReceipt(BaseModel):
    """Fenced execution receipt contract returned by a remote worker."""

    receipt_id: str = Field(min_length=1, max_length=160)
    dispatch_id: str = Field(min_length=1, max_length=160)
    worker_id: str = Field(min_length=1, max_length=160)
    run_id: str = Field(min_length=1, max_length=160)
    work_item_id: str = Field(min_length=1, max_length=160)
    fence_token: int = Field(ge=1)
    actor: str = Field(min_length=1, max_length=160)
    role: HostRole
    passed: bool
    summary: str = Field(min_length=1, max_length=1000)
    output_manifest: ArtifactTransferManifest

    @field_validator("receipt_id", "dispatch_id", "worker_id", "run_id", "work_item_id", "actor")
    @classmethod
    def validate_identifiers(cls, value: str, info) -> str:
        return _validate_safe_identifier(value, info.field_name)

    @model_validator(mode="after")
    def validate_output_manifest_binding(self) -> RemoteExecutionReceipt:
        _validate_manifest_binding(
            self.output_manifest,
            manifest_name="output_manifest",
            run_id=self.run_id,
            work_item_id=self.work_item_id,
            fence_token=self.fence_token,
        )
        return self


class RemoteWorkerPoolPolicy(BaseModel):
    """Policy governing opt-in remote worker admission and enterprise RBAC controls."""

    mode: WorkerExecutionMode = WorkerExecutionMode.LOCAL_FIRST
    remote_workers_enabled: bool = False
    require_operator_intent_for_remote: bool = True
    allowed_auth_methods: list[WorkerAuthMethod] = Field(
        default_factory=lambda: [WorkerAuthMethod.MTLS, WorkerAuthMethod.OIDC]
    )
    rbac_release_guardian_groups: list[str] = Field(default_factory=list)

    @field_validator("rbac_release_guardian_groups")
    @classmethod
    def validate_rbac_groups(cls, values: list[str]) -> list[str]:
        cleaned: list[str] = []
        for group in values:
            stripped = group.strip()
            if not stripped:
                continue
            _reject_control_separators(stripped, "rbac_release_guardian_groups")
            cleaned.append(stripped)
        return cleaned


class RemoteWorkerAdmissionController:
    """Fail-closed admission and RBAC evaluator for operator-hosted remote workers."""

    def __init__(self, policy: RemoteWorkerPoolPolicy | None = None) -> None:
        self.policy = policy or RemoteWorkerPoolPolicy()

    def admit_worker(
        self,
        worker: RemoteWorkerIdentity,
        *,
        operator_intent_ref: str | None = None,
    ) -> tuple[bool, list[str]]:
        """Evaluate whether a remote worker may be admitted under current pool policy."""
        reasons: list[str] = []

        if not self.policy.remote_workers_enabled:
            reasons.append(
                "remote workers are disabled (local-first default); explicit opt-in is required"
            )

        if self.policy.require_operator_intent_for_remote:
            if operator_intent_ref is None or not operator_intent_ref.strip():
                reasons.append("operator_intent_ref is required when remote admission is enabled")
            elif any(sep in operator_intent_ref for sep in _CONTROL_SEPARATORS):
                reasons.append("operator_intent_ref must not contain control separators")

        if worker.auth_method not in self.policy.allowed_auth_methods:
            reasons.append(
                f"auth_method '{worker.auth_method.value}' is not permitted by remote worker pool policy"
            )

        return (len(reasons) == 0, reasons)

    def authorize_release_guardian_group(self, actor_groups: list[str]) -> bool:
        """Check whether an actor's RBAC groups match configured release-guardian groups."""
        configured = {group.strip() for group in self.policy.rbac_release_guardian_groups if group.strip()}
        if not configured:
            return False
        presented = {group.strip() for group in actor_groups if group and group.strip()}
        return bool(configured & presented)

    def authorize_dispatch(
        self,
        worker: RemoteWorkerIdentity,
        envelope: RemoteDispatchEnvelope,
        *,
        active_fence_token: int | None = None,
        builder_actor: str | None = None,
    ) -> tuple[bool, list[str]]:
        """Authorize dispatching a fenced envelope to an admitted remote worker."""
        _, reasons = self.admit_worker(worker, operator_intent_ref=envelope.operator_intent_ref)

        if envelope.worker_id != worker.worker_id:
            reasons.append(
                f"envelope worker_id '{envelope.worker_id}' does not match worker '{worker.worker_id}'"
            )

        if worker.role != envelope.required_role:
            reasons.append(
                f"worker role '{worker.role.value}' does not match required_role '{envelope.required_role.value}'"
            )

        if envelope.risk_tier not in worker.allowed_risk_tiers:
            reasons.append(
                f"worker '{worker.worker_id}' is not authorized for risk_tier '{envelope.risk_tier.value}'"
            )

        if active_fence_token is not None and envelope.fence_token < active_fence_token:
            reasons.append(
                f"stale fence_token {envelope.fence_token} is less than active_fence_token {active_fence_token}"
            )

        if envelope.required_role == HostRole.VERIFIER and builder_actor is not None:
            builder_id = builder_actor.strip()
            if builder_id and (worker.worker_id == builder_id or worker.identity_subject == builder_id):
                reasons.append(
                    f"separation of duties violation: verifier '{worker.worker_id}' matches builder_actor '{builder_id}'"
                )

        return (len(reasons) == 0, reasons)

    def verify_execution_receipt(
        self,
        worker: RemoteWorkerIdentity,
        envelope: RemoteDispatchEnvelope,
        receipt: RemoteExecutionReceipt,
        *,
        output_payloads: dict[str, bytes] | None = None,
        active_fence_token: int | None = None,
    ) -> tuple[bool, list[str]]:
        """Verify a remote execution receipt against worker identity, dispatch envelope, and payloads."""
        reasons: list[str] = []

        if receipt.dispatch_id != envelope.dispatch_id:
            reasons.append(
                f"receipt dispatch_id '{receipt.dispatch_id}' does not match envelope '{envelope.dispatch_id}'"
            )
        if receipt.worker_id != worker.worker_id:
            reasons.append(
                f"receipt worker_id '{receipt.worker_id}' does not match worker '{worker.worker_id}'"
            )
        if receipt.run_id != envelope.run_id:
            reasons.append(
                f"receipt run_id '{receipt.run_id}' does not match envelope '{envelope.run_id}'"
            )
        if receipt.work_item_id != envelope.work_item_id:
            reasons.append(
                f"receipt work_item_id '{receipt.work_item_id}' does not match envelope '{envelope.work_item_id}'"
            )
        if receipt.role != envelope.required_role:
            reasons.append(
                f"receipt role '{receipt.role.value}' does not match required_role '{envelope.required_role.value}'"
            )
        if receipt.fence_token != envelope.fence_token:
            reasons.append(
                f"receipt fence_token {receipt.fence_token} does not match envelope fence_token {envelope.fence_token}"
            )
        if active_fence_token is not None and receipt.fence_token != active_fence_token:
            reasons.append(
                f"receipt fence_token {receipt.fence_token} does not match active_fence_token {active_fence_token}"
            )

        if output_payloads is not None:
            bundle_ok, bundle_failures = receipt.output_manifest.verify_bundle(output_payloads)
            if not bundle_ok:
                reasons.extend(bundle_failures)

        return (len(reasons) == 0, reasons)
