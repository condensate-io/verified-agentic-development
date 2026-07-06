from __future__ import annotations

from dataclasses import dataclass

from vad.control_plane.clients import ClientRuntimeStatus, ClientStatusSnapshot, ClientTrustState
from vad.control_plane.events import ControlPlaneEvent, ControlPlaneEventKind, ControlPlaneEventStatus
from vad.control_plane.leases import TaskLeaseStatus
from vad.control_plane.run_task_state import RunTaskState
from vad.control_plane.work_items import WorkItem, WorkItemStatus, update_work_item
from vad.policy.decisions import PolicyDecision
from vad.server.db.store import ServerStore


ALLOWED_WORK_ITEM_TRANSITIONS: dict[WorkItemStatus, set[WorkItemStatus]] = {
    WorkItemStatus.PLANNED: {WorkItemStatus.QUEUED, WorkItemStatus.CANCELLED},
    WorkItemStatus.QUEUED: {
        WorkItemStatus.ASSIGNED,
        WorkItemStatus.BLOCKED,
        WorkItemStatus.WAITING_FOR_HUMAN,
        WorkItemStatus.CANCELLED,
    },
    WorkItemStatus.ASSIGNED: {
        WorkItemStatus.RUNNING,
        WorkItemStatus.BLOCKED,
        WorkItemStatus.WAITING_FOR_HUMAN,
        WorkItemStatus.REQUEUED,
        WorkItemStatus.CANCELLED,
    },
    WorkItemStatus.RUNNING: {
        WorkItemStatus.BLOCKED,
        WorkItemStatus.WAITING_FOR_HUMAN,
        WorkItemStatus.VERIFYING,
        WorkItemStatus.COMPLETED,
        WorkItemStatus.FAILED,
        WorkItemStatus.CANCELLED,
        WorkItemStatus.REQUEUED,
    },
    WorkItemStatus.BLOCKED: {
        WorkItemStatus.QUEUED,
        WorkItemStatus.REQUEUED,
        WorkItemStatus.WAITING_FOR_HUMAN,
        WorkItemStatus.CANCELLED,
    },
    WorkItemStatus.WAITING_FOR_HUMAN: {
        WorkItemStatus.QUEUED,
        WorkItemStatus.ASSIGNED,
        WorkItemStatus.RUNNING,
        WorkItemStatus.REQUEUED,
        WorkItemStatus.CANCELLED,
    },
    WorkItemStatus.VERIFYING: {
        WorkItemStatus.APPROVED,
        WorkItemStatus.FAILED,
        WorkItemStatus.BLOCKED,
        WorkItemStatus.WAITING_FOR_HUMAN,
        WorkItemStatus.REQUEUED,
    },
    WorkItemStatus.APPROVED: {WorkItemStatus.COMPLETED},
    WorkItemStatus.FAILED: {WorkItemStatus.REQUEUED, WorkItemStatus.CANCELLED},
    WorkItemStatus.REQUEUED: {
        WorkItemStatus.QUEUED,
        WorkItemStatus.ASSIGNED,
        WorkItemStatus.CANCELLED,
    },
    WorkItemStatus.COMPLETED: set(),
    WorkItemStatus.CANCELLED: set(),
}


@dataclass(frozen=True)
class WorkItemTransitionResult:
    status_code: int
    work_item: WorkItem
    event: ControlPlaneEvent
    decision: PolicyDecision


@dataclass(frozen=True)
class SchedulerDecisionResult:
    status_code: int
    work_item: WorkItem | None
    selected_client_id: str | None
    event: ControlPlaneEvent | None
    decision: PolicyDecision


class WorkItemService:
    def __init__(self, store: ServerStore):
        self.store = store

    def create(
        self,
        item: WorkItem,
        *,
        actor: str,
        role: str,
        client_id: str = "control-plane",
        summary: str | None = None,
    ) -> WorkItemTransitionResult:
        saved = self.store.save_work_item(item)
        self.store.upsert_run_task_state(RunTaskState.from_work_item(saved))
        event = self._append_event(
            saved,
            actor=actor,
            role=role,
            client_id=client_id,
            summary=summary or f"Work item {saved.work_item_id} created as {saved.status.value}.",
        )
        return WorkItemTransitionResult(
            status_code=201,
            work_item=saved,
            event=event,
            decision=PolicyDecision(allow=True, reasons=["work item created"]),
        )

    def transition(
        self,
        work_item_id: str,
        *,
        status: WorkItemStatus,
        actor: str,
        role: str,
        client_id: str,
        assigned_client_id: str | None = None,
        lease_id: str | None = None,
        evidence_digest: str | None = None,
        blocked_reason: str | None = None,
        summary: str | None = None,
    ) -> WorkItemTransitionResult:
        current = self.store.load_work_item(work_item_id)
        decision = self._transition_decision(current.status, status)
        if not decision.allow:
            event = self._append_denial_event(
                current,
                actor=actor,
                role=role,
                client_id=client_id,
                target_status=status,
                decision=decision,
            )
            return WorkItemTransitionResult(
                status_code=409,
                work_item=current,
                event=event,
                decision=decision,
            )

        updated = update_work_item(
            current,
            status=status,
            assigned_client_id=assigned_client_id,
            lease_id=lease_id,
            evidence_digest=evidence_digest,
            blocked_reason=blocked_reason,
        )
        if status == WorkItemStatus.REQUEUED:
            updated = updated.model_copy(update={"assigned_client_id": None, "lease_id": None})
        saved = self.store.update_work_item(updated)
        self.store.upsert_run_task_state(RunTaskState.from_work_item(saved))
        event = self._append_event(
            saved,
            actor=actor,
            role=role,
            client_id=client_id,
            summary=summary or f"Work item {saved.work_item_id} transitioned to {saved.status.value}.",
        )
        return WorkItemTransitionResult(status_code=200, work_item=saved, event=event, decision=decision)

    def assign(
        self,
        work_item_id: str,
        *,
        actor: str,
        role: str,
        client_id: str,
        assigned_client_id: str,
        lease_id: str | None = None,
        summary: str | None = None,
    ) -> WorkItemTransitionResult:
        return self.transition(
            work_item_id,
            status=WorkItemStatus.ASSIGNED,
            actor=actor,
            role=role,
            client_id=client_id,
            assigned_client_id=assigned_client_id,
            lease_id=lease_id,
            summary=summary or f"Work item {work_item_id} assigned to {assigned_client_id}.",
        )

    def start(self, work_item_id: str, *, actor: str, role: str, client_id: str) -> WorkItemTransitionResult:
        return self.transition(work_item_id, status=WorkItemStatus.RUNNING, actor=actor, role=role, client_id=client_id)

    def block(
        self,
        work_item_id: str,
        *,
        actor: str,
        role: str,
        client_id: str,
        reason: str,
    ) -> WorkItemTransitionResult:
        return self.transition(
            work_item_id,
            status=WorkItemStatus.BLOCKED,
            actor=actor,
            role=role,
            client_id=client_id,
            blocked_reason=reason,
            summary=f"Work item {work_item_id} blocked: {reason}",
        )

    def wait_for_human(self, work_item_id: str, *, actor: str, role: str, client_id: str) -> WorkItemTransitionResult:
        return self.transition(
            work_item_id,
            status=WorkItemStatus.WAITING_FOR_HUMAN,
            actor=actor,
            role=role,
            client_id=client_id,
        )

    def verify(self, work_item_id: str, *, actor: str, role: str, client_id: str) -> WorkItemTransitionResult:
        return self.transition(work_item_id, status=WorkItemStatus.VERIFYING, actor=actor, role=role, client_id=client_id)

    def approve(self, work_item_id: str, *, actor: str, role: str, client_id: str) -> WorkItemTransitionResult:
        return self.transition(work_item_id, status=WorkItemStatus.APPROVED, actor=actor, role=role, client_id=client_id)

    def complete(
        self,
        work_item_id: str,
        *,
        actor: str,
        role: str,
        client_id: str,
        evidence_digest: str | None = None,
    ) -> WorkItemTransitionResult:
        return self.transition(
            work_item_id,
            status=WorkItemStatus.COMPLETED,
            actor=actor,
            role=role,
            client_id=client_id,
            evidence_digest=evidence_digest,
        )

    def fail(self, work_item_id: str, *, actor: str, role: str, client_id: str) -> WorkItemTransitionResult:
        return self.transition(work_item_id, status=WorkItemStatus.FAILED, actor=actor, role=role, client_id=client_id)

    def cancel(self, work_item_id: str, *, actor: str, role: str, client_id: str) -> WorkItemTransitionResult:
        return self.transition(work_item_id, status=WorkItemStatus.CANCELLED, actor=actor, role=role, client_id=client_id)

    def requeue(
        self,
        work_item_id: str,
        *,
        actor: str,
        role: str,
        client_id: str,
        summary: str | None = None,
    ) -> WorkItemTransitionResult:
        return self.transition(
            work_item_id,
            status=WorkItemStatus.REQUEUED,
            actor=actor,
            role=role,
            client_id=client_id,
            summary=summary,
        )

    def _transition_decision(self, source: WorkItemStatus, target: WorkItemStatus) -> PolicyDecision:
        if target in ALLOWED_WORK_ITEM_TRANSITIONS[source]:
            return PolicyDecision(allow=True, reasons=[f"work item transition {source.value}->{target.value} allowed"])
        return PolicyDecision(
            allow=False,
            denials=[f"work item transition {source.value}->{target.value} is not allowed"],
            requires_human=True,
        )

    def _append_event(
        self,
        item: WorkItem,
        *,
        actor: str,
        role: str,
        client_id: str,
        summary: str,
    ) -> ControlPlaneEvent:
        event = ControlPlaneEvent(
            sequence=len(self.store.list_control_plane_events()) + 1,
            client_id=client_id,
            run_id=item.run_id,
            task_id=item.work_item_id,
            kind=ControlPlaneEventKind.WORK_ITEM,
            status=_event_status(item.status),
            actor=actor,
            role=role,
            evidence_digest=item.evidence_digest,
            summary=summary,
        )
        self.store.append_control_plane_event(event)
        return event

    def _append_denial_event(
        self,
        item: WorkItem,
        *,
        actor: str,
        role: str,
        client_id: str,
        target_status: WorkItemStatus,
        decision: PolicyDecision,
    ) -> ControlPlaneEvent:
        event = ControlPlaneEvent(
            sequence=len(self.store.list_control_plane_events()) + 1,
            client_id=client_id,
            run_id=item.run_id,
            task_id=item.work_item_id,
            kind=ControlPlaneEventKind.POLICY_DENIED,
            status=ControlPlaneEventStatus.BLOCKED,
            actor=actor,
            role=role,
            summary=f"Policy denied work_item transition to {target_status.value}: {'; '.join(decision.denials)}",
        )
        self.store.append_control_plane_event(event)
        return event


class WorkItemSchedulerService:
    def __init__(self, store: ServerStore):
        self.store = store
        self.work_items = WorkItemService(store)

    def schedule_next(
        self,
        *,
        run_id: str | None = None,
        actor: str = "scheduler",
        role: str = "operator",
        client_id: str = "control-plane",
    ) -> SchedulerDecisionResult:
        candidates = [
            item
            for item in self.store.list_work_items(run_id=run_id)
            if item.status in {WorkItemStatus.QUEUED, WorkItemStatus.REQUEUED}
        ]
        if not candidates:
            return SchedulerDecisionResult(
                status_code=404,
                work_item=None,
                selected_client_id=None,
                event=None,
                decision=PolicyDecision(
                    allow=False,
                    denials=["no queued work items available for scheduling"],
                    requires_human=False,
                ),
            )
        return self.schedule_work_item(
            candidates[0].work_item_id,
            actor=actor,
            role=role,
            client_id=client_id,
        )

    def schedule_work_item(
        self,
        work_item_id: str,
        *,
        actor: str = "scheduler",
        role: str = "operator",
        client_id: str = "control-plane",
    ) -> SchedulerDecisionResult:
        item = self.store.load_work_item(work_item_id)
        if item.status not in {WorkItemStatus.QUEUED, WorkItemStatus.REQUEUED}:
            decision = PolicyDecision(
                allow=False,
                denials=[f"work item {item.work_item_id} is {item.status.value}, not schedulable"],
                requires_human=True,
            )
            event = self._append_denial_event(item, actor=actor, role=role, client_id=client_id, decision=decision)
            return SchedulerDecisionResult(
                status_code=409,
                work_item=item,
                selected_client_id=None,
                event=event,
                decision=decision,
            )

        eligible = self._eligible_clients(item)
        if not eligible:
            decision = PolicyDecision(
                allow=False,
                denials=[f"no active trusted clients match work item {item.work_item_id}"],
                requires_human=True,
            )
            event = self._append_denial_event(item, actor=actor, role=role, client_id=client_id, decision=decision)
            return SchedulerDecisionResult(
                status_code=409,
                work_item=item,
                selected_client_id=None,
                event=event,
                decision=decision,
            )

        selected = eligible[0]
        assignment = self.work_items.assign(
            item.work_item_id,
            actor=actor,
            role=role,
            client_id=client_id,
            assigned_client_id=selected.manifest.client_id,
            summary=f"Scheduler assigned work item {item.work_item_id} to {selected.manifest.client_id}.",
        )
        return SchedulerDecisionResult(
            status_code=assignment.status_code,
            work_item=assignment.work_item,
            selected_client_id=selected.manifest.client_id,
            event=assignment.event,
            decision=PolicyDecision(
                allow=True,
                reasons=[f"scheduler assigned {item.work_item_id} to {selected.manifest.client_id}"],
            ),
        )

    def _eligible_clients(self, item: WorkItem) -> list[ClientStatusSnapshot]:
        snapshots = [
            snapshot
            for snapshot in self.store.list_client_statuses()
            if snapshot.status == ClientRuntimeStatus.ACTIVE
            and snapshot.manifest.trust_state == ClientTrustState.TRUSTED
            and self._supports_requested_capability(snapshot, item)
        ]
        snapshots.sort(key=lambda snapshot: (
            len(self.store.list_task_leases(
                client_id=snapshot.manifest.client_id,
                status=TaskLeaseStatus.ACTIVE,
            )),
            snapshot.manifest.display_name,
            snapshot.manifest.client_id,
        ))
        return snapshots

    def _supports_requested_capability(self, snapshot: ClientStatusSnapshot, item: WorkItem) -> bool:
        if item.requested_capability is None:
            return True
        return item.requested_capability in snapshot.manifest.supported_capabilities

    def _append_denial_event(
        self,
        item: WorkItem,
        *,
        actor: str,
        role: str,
        client_id: str,
        decision: PolicyDecision,
    ) -> ControlPlaneEvent:
        event = ControlPlaneEvent(
            sequence=len(self.store.list_control_plane_events()) + 1,
            client_id=client_id,
            run_id=item.run_id,
            task_id=item.work_item_id,
            kind=ControlPlaneEventKind.POLICY_DENIED,
            status=ControlPlaneEventStatus.BLOCKED,
            actor=actor,
            role=role,
            summary=f"Policy denied scheduler assignment: {'; '.join(decision.denials)}",
        )
        self.store.append_control_plane_event(event)
        return event


def _event_status(status: WorkItemStatus) -> ControlPlaneEventStatus:
    if status in {WorkItemStatus.COMPLETED, WorkItemStatus.APPROVED}:
        return ControlPlaneEventStatus.PASSED
    if status in {WorkItemStatus.FAILED, WorkItemStatus.CANCELLED}:
        return ControlPlaneEventStatus.FAILED
    if status == WorkItemStatus.BLOCKED:
        return ControlPlaneEventStatus.BLOCKED
    if status == WorkItemStatus.WAITING_FOR_HUMAN:
        return ControlPlaneEventStatus.NEEDS_HUMAN
    return ControlPlaneEventStatus.ACTIVE


__all__ = [
    "ALLOWED_WORK_ITEM_TRANSITIONS",
    "SchedulerDecisionResult",
    "WorkItemSchedulerService",
    "WorkItemService",
    "WorkItemTransitionResult",
]
