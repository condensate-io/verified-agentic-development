from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from vad.control_plane.events import ControlPlaneEvent, ControlPlaneEventKind, ControlPlaneEventStatus
from vad.control_plane.leases import (
    TaskLease,
    TaskLeaseAcquireRequest,
    TaskLeaseApprovalCheck,
    TaskLeaseReleaseRequest,
    TaskLeaseRenewRequest,
    TaskLeaseStatus,
    new_task_lease,
)
from vad.policy.decisions import PolicyDecision
from vad.server.db.store import ServerStore


@dataclass(frozen=True)
class TaskLeaseResult:
    status_code: int
    lease: TaskLease
    event: ControlPlaneEvent
    decision: PolicyDecision


class TaskLeaseService:
    def __init__(self, store: ServerStore):
        self.store = store

    def acquire(self, request: TaskLeaseAcquireRequest) -> TaskLeaseResult:
        self.store.load_client_manifest(request.client_id)
        try:
            existing = self.store.load_task_lease(request.task_id)
        except KeyError:
            existing = None
        if existing is not None and existing.status == TaskLeaseStatus.ACTIVE:
            decision = PolicyDecision(
                allow=False,
                denials=[f"task {request.task_id} already leased by {existing.client_id}"],
                requires_human=True,
            )
            return TaskLeaseResult(
                status_code=409,
                lease=existing,
                event=self._append_event(existing, "Lease acquire denied because task is already active.", ControlPlaneEventStatus.BLOCKED),
                decision=decision,
            )
        lease = self.store.save_task_lease(new_task_lease(request))
        return TaskLeaseResult(
            status_code=201,
            lease=lease,
            event=self._append_event(lease, "Task lease acquired.", ControlPlaneEventStatus.ACTIVE),
            decision=PolicyDecision(allow=True, reasons=["task lease acquired"]),
        )

    def renew(self, task_id: str, request: TaskLeaseRenewRequest) -> TaskLeaseResult:
        lease = self.store.load_task_lease(task_id)
        self._require_holder(lease, request.client_id)
        lease.renew(now=datetime.now(timezone.utc), ttl_seconds=request.ttl_seconds)
        self.store.save_task_lease(lease)
        return TaskLeaseResult(
            status_code=200,
            lease=lease,
            event=self._append_event(lease, "Task lease renewed.", ControlPlaneEventStatus.ACTIVE),
            decision=PolicyDecision(allow=True, reasons=["task lease renewed"]),
        )

    def release(self, task_id: str, request: TaskLeaseReleaseRequest) -> TaskLeaseResult:
        lease = self.store.load_task_lease(task_id)
        self._require_holder(lease, request.client_id)
        lease.release(reason=request.reason)
        self.store.save_task_lease(lease)
        return TaskLeaseResult(
            status_code=200,
            lease=lease,
            event=self._append_event(lease, "Task lease released.", ControlPlaneEventStatus.PASSED),
            decision=PolicyDecision(allow=True, reasons=["task lease released"]),
        )

    def expire(self, task_id: str) -> TaskLeaseResult:
        lease = self.store.load_task_lease(task_id)
        lease.status = TaskLeaseStatus.EXPIRED
        lease.release_reason = "lease expired"
        self.store.save_task_lease(lease)
        return TaskLeaseResult(
            status_code=200,
            lease=lease,
            event=self._append_event(lease, "Task lease expired.", ControlPlaneEventStatus.STALE),
            decision=PolicyDecision(allow=True, reasons=["task lease expired"]),
        )

    def check_approval_transition(self, task_id: str, request: TaskLeaseApprovalCheck) -> PolicyDecision:
        lease = self.store.load_task_lease(task_id)
        denials = []
        if request.action != "approve_release":
            denials.append("unsupported lease approval action")
        try:
            lease.assert_approval_transition_allowed(actor=request.actor, role=request.role)
        except ValueError as exc:
            denials.append(str(exc))
        if denials:
            return PolicyDecision(allow=False, denials=denials, requires_human=True)
        return PolicyDecision(allow=True, reasons=["lease approval transition allowed"])

    def _require_holder(self, lease: TaskLease, client_id: str) -> None:
        if lease.client_id != client_id:
            raise PermissionError(f"task lease is held by {lease.client_id}")
        if lease.status != TaskLeaseStatus.ACTIVE:
            raise ValueError(f"task lease is {lease.status.value}")

    def _append_event(self, lease: TaskLease, summary: str, status: ControlPlaneEventStatus) -> ControlPlaneEvent:
        event = ControlPlaneEvent(
            sequence=len(self.store.list_control_plane_events()) + 1,
            client_id=lease.client_id,
            run_id=lease.run_id,
            task_id=lease.task_id,
            kind=ControlPlaneEventKind.TASK_LEASE,
            status=status,
            actor=lease.actor,
            role=lease.role,
            summary=summary,
        )
        self.store.append_control_plane_event(event)
        return event
