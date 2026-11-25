"""Coordinator for opt-in operator-hosted remote workers and RBAC release gates."""

from __future__ import annotations

import inspect
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field

from vad.contracts.models import RiskTier
from vad.control_plane.remote_workers import (
    ArtifactTransferManifest,
    RemoteDispatchEnvelope,
    RemoteExecutionReceipt,
    RemoteWorkerAdmissionController,
    RemoteWorkerIdentity,
    RemoteWorkerPoolPolicy,
)
from vad.control_plane.sessions import (
    HostSessionClaims,
    HostSessionManager,
    HostSessionToken,
)

_DEFAULT_SESSION_SIGNING_KEY = b"vad-remote-worker-default-session-key"
_DEFAULT_SESSION_TTL = timedelta(hours=1)
_RBAC_REQUIRED_GUARDIANS: dict[RiskTier, int] = {
    RiskTier.LOW: 0,
    RiskTier.MEDIUM: 0,
    RiskTier.HIGH: 1,
    RiskTier.CRITICAL: 2,
}


class RemoteDispatchSession(BaseModel):
    """Issued dispatch envelope paired with the admitted worker and scoped session token."""

    envelope: RemoteDispatchEnvelope
    worker: RemoteWorkerIdentity
    session_token: HostSessionToken


class RemoteReceiptVerificationResult(BaseModel):
    """Outcome of verifying a fenced remote execution receipt and its output manifest."""

    verified: bool
    receipt_id: str
    dispatch_id: str
    worker_id: str
    fence_token: int
    reasons: list[str] = Field(default_factory=list)


class RBACReleaseAuthorizationResult(BaseModel):
    """Outcome of RBAC release-guardian co-authorization with separation of duties."""

    authorized: bool
    approved_guardians: list[str] = Field(default_factory=list)
    denied_actors: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)


class RemoteWorkerCoordinator:
    """Coordinates opt-in remote worker registration, fenced dispatch, receipt intake, and RBAC."""

    def __init__(
        self,
        *,
        policy: RemoteWorkerPoolPolicy | None = None,
        session_manager: HostSessionManager | None = None,
        store: Any | None = None,
    ) -> None:
        self.policy = policy or RemoteWorkerPoolPolicy()
        self.admission = RemoteWorkerAdmissionController(self.policy)
        self.session_manager = session_manager or HostSessionManager(
            signing_secret=_DEFAULT_SESSION_SIGNING_KEY
        )
        self.store = store
        self._workers: dict[str, RemoteWorkerIdentity] = {}
        self._dispatches: dict[str, RemoteDispatchEnvelope] = {}
        self._active_fences: dict[str, int] = {}
        self._manifests: dict[str, ArtifactTransferManifest] = {}

        self._hydrate_workers_from_store()

    @staticmethod
    def _coerce_worker(item: Any) -> RemoteWorkerIdentity | None:
        if item is None:
            return None
        if isinstance(item, RemoteWorkerIdentity):
            return item
        if isinstance(item, dict):
            return RemoteWorkerIdentity.model_validate(item)
        return RemoteWorkerIdentity.model_validate(item, from_attributes=True)

    def _hydrate_workers_from_store(self) -> None:
        if self.store is None:
            return
        list_hook = getattr(self.store, "list_remote_workers", None)
        if not callable(list_hook):
            return
        for item in list_hook():
            worker = self._coerce_worker(item)
            if worker is not None:
                self._workers[worker.worker_id] = worker

    def _persist_worker(
        self,
        worker: RemoteWorkerIdentity,
        *,
        operator_intent_ref: str | None,
    ) -> None:
        if self.store is None:
            return
        save_hook = getattr(self.store, "save_remote_worker", None)
        if not callable(save_hook):
            return
        try:
            sig = inspect.signature(save_hook)
            accepts_intent = "operator_intent_ref" in sig.parameters or any(
                param.kind == inspect.Parameter.VAR_KEYWORD
                for param in sig.parameters.values()
            )
        except (TypeError, ValueError):
            accepts_intent = True

        if accepts_intent:
            save_hook(worker, operator_intent_ref=operator_intent_ref)
        else:
            save_hook(worker)

    def _persist_manifest(self, manifest: ArtifactTransferManifest) -> None:
        if self.store is None:
            return
        save_hook = getattr(self.store, "save_artifact_transfer_manifest", None)
        if callable(save_hook):
            save_hook(manifest)

    def register_worker(
        self,
        worker: RemoteWorkerIdentity,
        *,
        operator_intent_ref: str | None = None,
    ) -> tuple[bool, list[str]]:
        """Admit and register an operator-hosted remote worker under pool policy."""
        allowed, reasons = self.admission.admit_worker(
            worker,
            operator_intent_ref=operator_intent_ref,
        )
        if not allowed:
            return False, reasons

        self._workers[worker.worker_id] = worker
        self._persist_worker(worker, operator_intent_ref=operator_intent_ref)
        return True, []

    def get_worker(self, worker_id: str) -> RemoteWorkerIdentity:
        """Return a registered remote worker or raise KeyError if unknown."""
        worker = self._workers.get(worker_id)
        if worker is not None:
            return worker

        if self.store is not None:
            load_hook = getattr(self.store, "load_remote_worker", None)
            if callable(load_hook):
                loaded = load_hook(worker_id)
                coerced = self._coerce_worker(loaded)
                if coerced is not None:
                    self._workers[coerced.worker_id] = coerced
                    return coerced

        raise KeyError(f"remote worker '{worker_id}' is not registered")

    def list_workers(self) -> list[RemoteWorkerIdentity]:
        """List all registered remote workers."""
        self._hydrate_workers_from_store()
        return list(self._workers.values())

    def dispatch_work(
        self,
        envelope: RemoteDispatchEnvelope,
        *,
        input_payloads: dict[str, bytes] | None = None,
        builder_actor: str | None = None,
    ) -> RemoteDispatchSession:
        """Authorize a fenced dispatch, verify optional input payloads, and issue a session token."""
        try:
            worker = self.get_worker(envelope.worker_id)
        except KeyError as exc:
            raise PermissionError(
                f"remote worker '{envelope.worker_id}' is not registered"
            ) from exc

        active_fence = self._active_fences.get(envelope.work_item_id)
        allowed, reasons = self.admission.authorize_dispatch(
            worker,
            envelope,
            active_fence_token=active_fence,
            builder_actor=builder_actor,
        )
        if not allowed:
            raise PermissionError("; ".join(reasons))

        if input_payloads is not None:
            bundle_ok, bundle_reasons = envelope.input_manifest.verify_bundle(input_payloads)
            if not bundle_ok:
                raise ValueError("; ".join(bundle_reasons))

        self._active_fences[envelope.work_item_id] = envelope.fence_token
        self._dispatches[envelope.dispatch_id] = envelope
        self._manifests[envelope.input_manifest.transfer_id] = envelope.input_manifest
        self._persist_manifest(envelope.input_manifest)

        now_utc = datetime.now(timezone.utc)
        token = self.session_manager.issue(
            HostSessionClaims(
                session_id=f"sess-{uuid4().hex[:12]}",
                client_id=worker.worker_id,
                host_type=worker.host_type,
                role=envelope.required_role,
                actor=worker.worker_id,
                work_item_id=envelope.work_item_id,
                fence_token=envelope.fence_token,
                issued_at=now_utc,
                expires_at=now_utc + _DEFAULT_SESSION_TTL,
            )
        )
        return RemoteDispatchSession(
            envelope=envelope,
            worker=worker,
            session_token=token,
        )

    def ingest_execution_receipt(
        self,
        receipt: RemoteExecutionReceipt,
        *,
        output_payloads: dict[str, bytes] | None = None,
    ) -> RemoteReceiptVerificationResult:
        """Verify a remote worker receipt against the active dispatch, fence, and output bundle."""
        envelope = self._dispatches.get(receipt.dispatch_id)
        try:
            worker = self.get_worker(receipt.worker_id)
        except KeyError:
            worker = None

        if envelope is None or worker is None:
            return RemoteReceiptVerificationResult(
                verified=False,
                receipt_id=receipt.receipt_id,
                dispatch_id=receipt.dispatch_id,
                worker_id=receipt.worker_id,
                fence_token=receipt.fence_token,
                reasons=["unknown dispatch_id or worker_id"],
            )

        active_fence = self._active_fences.get(receipt.work_item_id)
        ok, reasons = self.admission.verify_execution_receipt(
            worker,
            envelope,
            receipt,
            output_payloads=output_payloads,
            active_fence_token=active_fence,
        )
        if ok:
            self._manifests[receipt.output_manifest.transfer_id] = receipt.output_manifest
            self._persist_manifest(receipt.output_manifest)

        return RemoteReceiptVerificationResult(
            verified=ok,
            receipt_id=receipt.receipt_id,
            dispatch_id=receipt.dispatch_id,
            worker_id=receipt.worker_id,
            fence_token=receipt.fence_token,
            reasons=reasons,
        )

    def authorize_release_with_rbac(
        self,
        *,
        actor_groups_by_guardian: dict[str, list[str]],
        risk_tier: RiskTier,
        builder_actor: str,
        verifier_actor: str,
    ) -> RBACReleaseAuthorizationResult:
        """Co-authorize release guardians via RBAC group membership and builder separation."""
        _ = verifier_actor  # Preserved in interface for release audit context.
        builder_id = builder_actor.strip()
        approved_guardians: list[str] = []
        denied_actors: list[str] = []
        reasons: list[str] = []

        for raw_guardian, groups in actor_groups_by_guardian.items():
            guardian_id = raw_guardian.strip()
            if not guardian_id:
                denied_actors.append(raw_guardian)
                reasons.append("guardian actor must not be empty")
                continue

            actor_denied = False
            if builder_id and guardian_id == builder_id:
                actor_denied = True
                reasons.append(
                    f"separation of duties violation: builder '{builder_id}' cannot self-approve release"
                )

            if not self.admission.authorize_release_guardian_group(groups):
                actor_denied = True
                reasons.append(
                    f"guardian '{guardian_id}' is not authorized by configured RBAC release-guardian groups"
                )

            if actor_denied:
                if guardian_id not in denied_actors:
                    denied_actors.append(guardian_id)
            elif guardian_id not in approved_guardians:
                approved_guardians.append(guardian_id)

        required_count = _RBAC_REQUIRED_GUARDIANS[risk_tier]
        if len(approved_guardians) < required_count:
            reasons.append(
                f"risk_tier '{risk_tier.value}' requires at least {required_count} authorized "
                f"release guardian(s), got {len(approved_guardians)}"
            )

        authorized = len(denied_actors) == 0 and len(approved_guardians) >= required_count
        return RBACReleaseAuthorizationResult(
            authorized=authorized,
            approved_guardians=approved_guardians,
            denied_actors=denied_actors,
            reasons=reasons,
        )
