from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from vad.control_plane.checkpoints import CheckpointStage, RunCheckpoint, next_resume_stage
from vad.control_plane.clients import ClientRuntimeStatus, ClientStatusSnapshot, ClientTrustState
from vad.control_plane.events import ControlPlaneEvent, ControlPlaneEventKind, ControlPlaneEventStatus
from vad.control_plane.idempotency import (
    IdempotencyRecord,
    IdempotencyStatus,
    compute_request_hash,
    evaluate_idempotency,
)
from vad.control_plane.leases import TaskLease, TaskLeaseAcquireRequest, TaskLeaseStatus
from vad.control_plane.run_task_state import RunTaskState
from vad.control_plane.work_items import WorkItem, WorkItemStatus, update_work_item
from vad.policy.decisions import PolicyDecision
from vad.server.db.store import ServerStore


_AUTO_CHECKPOINT_STAGES: dict[WorkItemStatus, CheckpointStage] = {
    WorkItemStatus.ASSIGNED: CheckpointStage.CONSTRUCTION_ASSIGNED,
    WorkItemStatus.VERIFYING: CheckpointStage.DIFF_PROPOSED,
    WorkItemStatus.APPROVED: CheckpointStage.VERIFIED,
    WorkItemStatus.COMPLETED: CheckpointStage.RELEASED,
}


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
    fence_token: int | None = None
    checkpoint: RunCheckpoint | None = None
    idempotency_replayed: bool = False


@dataclass(frozen=True)
class SchedulerDecisionResult:
    status_code: int
    work_item: WorkItem | None
    selected_client_id: str | None
    event: ControlPlaneEvent | None
    decision: PolicyDecision
    fence_token: int | None = None


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
        fence_token: int | None = None,
        idempotency_key: str | None = None,
        checkpoint_stage: CheckpointStage | None = None,
        artifact_digests: dict[str, str] | None = None,
    ) -> WorkItemTransitionResult:
        current = self.store.load_work_item(work_item_id)

        route = f"work_items/{work_item_id}/transition"
        request_hash: str | None = None
        if idempotency_key is not None:
            request_hash = compute_request_hash({
                "work_item_id": work_item_id,
                "status": status.value,
                "actor": actor,
                "role": role,
                "client_id": client_id,
                "assigned_client_id": assigned_client_id,
                "lease_id": lease_id,
                "evidence_digest": evidence_digest,
                "blocked_reason": blocked_reason,
                "fence_token": fence_token,
            })
            existing = self.store.load_idempotency_record(idempotency_key)
            idem_status = evaluate_idempotency(existing, route=route, request_hash=request_hash)
            if idem_status == IdempotencyStatus.REPLAY and existing is not None:
                return self._build_replay_result(existing, current=current, fallback_fence_token=fence_token)
            if idem_status == IdempotencyStatus.CONFLICT:
                conflict_decision = PolicyDecision(
                    allow=False,
                    denials=["idempotency key conflict: payload mismatch"],
                )
                conflict_event = self._append_denial_event(
                    current,
                    actor=actor,
                    role=role,
                    client_id=client_id,
                    target_status=status,
                    decision=conflict_decision,
                )
                return WorkItemTransitionResult(
                    status_code=409,
                    work_item=current,
                    event=conflict_event,
                    decision=conflict_decision,
                    fence_token=fence_token,
                )

        lease = self._resolve_lease(current, lease_id=lease_id)
        latest_cp = self.store.latest_checkpoint(current.run_id, work_item_id=work_item_id)
        if fence_token is not None:
            fence_denial = self._check_fence_token(fence_token, lease=lease, latest_checkpoint=latest_cp)
            if fence_denial is not None:
                denial_event = self._append_denial_event(
                    current,
                    actor=actor,
                    role=role,
                    client_id=client_id,
                    target_status=status,
                    decision=fence_denial,
                )
                return WorkItemTransitionResult(
                    status_code=409,
                    work_item=current,
                    event=denial_event,
                    decision=fence_denial,
                    fence_token=fence_token,
                )

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
                fence_token=fence_token,
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

        effective_fence_token = self._effective_fence_token(
            fence_token=fence_token,
            lease=lease,
            latest_checkpoint=latest_cp,
        )
        target_stage = checkpoint_stage if checkpoint_stage is not None else _AUTO_CHECKPOINT_STAGES.get(saved.status)
        checkpoint: RunCheckpoint | None = None
        if target_stage is not None:
            checkpoint = self.record_checkpoint(
                saved,
                stage=target_stage,
                actor=actor,
                role=role,
                fence_token=effective_fence_token,
                artifact_digests=artifact_digests,
            )

        if idempotency_key is not None and request_hash is not None:
            self.store.save_idempotency_record(
                IdempotencyRecord(
                    idempotency_key=idempotency_key,
                    route=route,
                    request_hash=request_hash,
                    status_code=200,
                    response_payload={
                        "work_item": saved.model_dump(mode="json"),
                        "event": event.model_dump(mode="json"),
                        "decision": decision.model_dump(mode="json"),
                        "fence_token": effective_fence_token,
                        "checkpoint": checkpoint.model_dump(mode="json") if checkpoint is not None else None,
                    },
                )
            )

        return WorkItemTransitionResult(
            status_code=200,
            work_item=saved,
            event=event,
            decision=decision,
            fence_token=effective_fence_token,
            checkpoint=checkpoint,
        )

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
        fence_token: int | None = None,
        idempotency_key: str | None = None,
        checkpoint_stage: CheckpointStage | None = None,
        artifact_digests: dict[str, str] | None = None,
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
            fence_token=fence_token,
            idempotency_key=idempotency_key,
            checkpoint_stage=checkpoint_stage,
            artifact_digests=artifact_digests,
        )

    def start(
        self,
        work_item_id: str,
        *,
        actor: str,
        role: str,
        client_id: str,
        fence_token: int | None = None,
        idempotency_key: str | None = None,
        checkpoint_stage: CheckpointStage | None = None,
        artifact_digests: dict[str, str] | None = None,
    ) -> WorkItemTransitionResult:
        return self.transition(
            work_item_id,
            status=WorkItemStatus.RUNNING,
            actor=actor,
            role=role,
            client_id=client_id,
            fence_token=fence_token,
            idempotency_key=idempotency_key,
            checkpoint_stage=checkpoint_stage,
            artifact_digests=artifact_digests,
        )

    def block(
        self,
        work_item_id: str,
        *,
        actor: str,
        role: str,
        client_id: str,
        reason: str,
        fence_token: int | None = None,
        idempotency_key: str | None = None,
        checkpoint_stage: CheckpointStage | None = None,
        artifact_digests: dict[str, str] | None = None,
    ) -> WorkItemTransitionResult:
        return self.transition(
            work_item_id,
            status=WorkItemStatus.BLOCKED,
            actor=actor,
            role=role,
            client_id=client_id,
            blocked_reason=reason,
            summary=f"Work item {work_item_id} blocked: {reason}",
            fence_token=fence_token,
            idempotency_key=idempotency_key,
            checkpoint_stage=checkpoint_stage,
            artifact_digests=artifact_digests,
        )

    def wait_for_human(
        self,
        work_item_id: str,
        *,
        actor: str,
        role: str,
        client_id: str,
        fence_token: int | None = None,
        idempotency_key: str | None = None,
        checkpoint_stage: CheckpointStage | None = None,
        artifact_digests: dict[str, str] | None = None,
    ) -> WorkItemTransitionResult:
        return self.transition(
            work_item_id,
            status=WorkItemStatus.WAITING_FOR_HUMAN,
            actor=actor,
            role=role,
            client_id=client_id,
            fence_token=fence_token,
            idempotency_key=idempotency_key,
            checkpoint_stage=checkpoint_stage,
            artifact_digests=artifact_digests,
        )

    def verify(
        self,
        work_item_id: str,
        *,
        actor: str,
        role: str,
        client_id: str,
        fence_token: int | None = None,
        idempotency_key: str | None = None,
        checkpoint_stage: CheckpointStage | None = None,
        artifact_digests: dict[str, str] | None = None,
    ) -> WorkItemTransitionResult:
        return self.transition(
            work_item_id,
            status=WorkItemStatus.VERIFYING,
            actor=actor,
            role=role,
            client_id=client_id,
            fence_token=fence_token,
            idempotency_key=idempotency_key,
            checkpoint_stage=checkpoint_stage,
            artifact_digests=artifact_digests,
        )

    def approve(
        self,
        work_item_id: str,
        *,
        actor: str,
        role: str,
        client_id: str,
        fence_token: int | None = None,
        idempotency_key: str | None = None,
        checkpoint_stage: CheckpointStage | None = None,
        artifact_digests: dict[str, str] | None = None,
    ) -> WorkItemTransitionResult:
        return self.transition(
            work_item_id,
            status=WorkItemStatus.APPROVED,
            actor=actor,
            role=role,
            client_id=client_id,
            fence_token=fence_token,
            idempotency_key=idempotency_key,
            checkpoint_stage=checkpoint_stage,
            artifact_digests=artifact_digests,
        )

    def complete(
        self,
        work_item_id: str,
        *,
        actor: str,
        role: str,
        client_id: str,
        evidence_digest: str | None = None,
        fence_token: int | None = None,
        idempotency_key: str | None = None,
        checkpoint_stage: CheckpointStage | None = None,
        artifact_digests: dict[str, str] | None = None,
    ) -> WorkItemTransitionResult:
        return self.transition(
            work_item_id,
            status=WorkItemStatus.COMPLETED,
            actor=actor,
            role=role,
            client_id=client_id,
            evidence_digest=evidence_digest,
            fence_token=fence_token,
            idempotency_key=idempotency_key,
            checkpoint_stage=checkpoint_stage,
            artifact_digests=artifact_digests,
        )

    def fail(
        self,
        work_item_id: str,
        *,
        actor: str,
        role: str,
        client_id: str,
        fence_token: int | None = None,
        idempotency_key: str | None = None,
        checkpoint_stage: CheckpointStage | None = None,
        artifact_digests: dict[str, str] | None = None,
    ) -> WorkItemTransitionResult:
        return self.transition(
            work_item_id,
            status=WorkItemStatus.FAILED,
            actor=actor,
            role=role,
            client_id=client_id,
            fence_token=fence_token,
            idempotency_key=idempotency_key,
            checkpoint_stage=checkpoint_stage,
            artifact_digests=artifact_digests,
        )

    def cancel(
        self,
        work_item_id: str,
        *,
        actor: str,
        role: str,
        client_id: str,
        fence_token: int | None = None,
        idempotency_key: str | None = None,
        checkpoint_stage: CheckpointStage | None = None,
        artifact_digests: dict[str, str] | None = None,
    ) -> WorkItemTransitionResult:
        return self.transition(
            work_item_id,
            status=WorkItemStatus.CANCELLED,
            actor=actor,
            role=role,
            client_id=client_id,
            fence_token=fence_token,
            idempotency_key=idempotency_key,
            checkpoint_stage=checkpoint_stage,
            artifact_digests=artifact_digests,
        )

    def requeue(
        self,
        work_item_id: str,
        *,
        actor: str,
        role: str,
        client_id: str,
        summary: str | None = None,
        fence_token: int | None = None,
        idempotency_key: str | None = None,
        checkpoint_stage: CheckpointStage | None = None,
        artifact_digests: dict[str, str] | None = None,
    ) -> WorkItemTransitionResult:
        return self.transition(
            work_item_id,
            status=WorkItemStatus.REQUEUED,
            actor=actor,
            role=role,
            client_id=client_id,
            summary=summary,
            fence_token=fence_token,
            idempotency_key=idempotency_key,
            checkpoint_stage=checkpoint_stage,
            artifact_digests=artifact_digests,
        )

    def record_checkpoint(
        self,
        work_item: WorkItem,
        *,
        stage: CheckpointStage,
        actor: str,
        role: str,
        fence_token: int | None = None,
        artifact_digests: dict[str, str] | None = None,
        state_snapshot: dict[str, Any] | None = None,
    ) -> RunCheckpoint:
        """Persist a durable RunCheckpoint for a work item stage."""
        digests = dict(artifact_digests) if artifact_digests is not None else {}
        if not digests and work_item.evidence_digest is not None:
            digests["evidence"] = work_item.evidence_digest
        snapshot = (
            dict(state_snapshot)
            if state_snapshot is not None
            else {
                "status": work_item.status.value,
                "assigned_client_id": work_item.assigned_client_id,
                "lease_id": work_item.lease_id,
                "evidence_digest": work_item.evidence_digest,
            }
        )
        effective_fence = fence_token if fence_token is not None and fence_token >= 1 else 1
        cp = RunCheckpoint(
            checkpoint_id=f"cp-{uuid4().hex}",
            run_id=work_item.run_id,
            work_item_id=work_item.work_item_id,
            stage=stage,
            fence_token=effective_fence,
            actor=actor,
            role=role,
            artifact_digests=digests,
            state_snapshot=snapshot,
        )
        return self.store.save_checkpoint(cp)

    def recover_from_checkpoint(
        self,
        run_id: str,
        work_item_id: str,
    ) -> tuple[RunCheckpoint | None, CheckpointStage | None]:
        """Load the latest durable checkpoint for a work item and determine the next resume stage."""
        latest = self.store.latest_checkpoint(run_id, work_item_id=work_item_id)
        return latest, next_resume_stage(latest)

    def _resolve_lease(self, item: WorkItem, *, lease_id: str | None = None) -> TaskLease | None:
        for candidate_task_id in dict.fromkeys(
            cid for cid in (item.work_item_id, lease_id, item.lease_id) if cid
        ):
            try:
                return self.store.load_task_lease(candidate_task_id)
            except KeyError:
                continue
        return None

    def _check_fence_token(
        self,
        fence_token: int,
        *,
        lease: TaskLease | None,
        latest_checkpoint: RunCheckpoint | None,
    ) -> PolicyDecision | None:
        if fence_token < 1:
            return PolicyDecision(
                allow=False,
                denials=[f"stale fence token rejected: fence_token {fence_token} must be >= 1"],
                requires_human=True,
            )
        if lease is not None:
            if lease.status == TaskLeaseStatus.ACTIVE and fence_token != lease.fence_token:
                return PolicyDecision(
                    allow=False,
                    denials=[
                        f"stale fence token rejected: presented {fence_token}, active lease expects {lease.fence_token}"
                    ],
                    requires_human=True,
                )
            if lease.status == TaskLeaseStatus.EXPIRED and fence_token <= lease.fence_token:
                return PolicyDecision(
                    allow=False,
                    denials=[
                        f"stale fence token rejected: lease expired at fence_token {lease.fence_token}"
                    ],
                    requires_human=True,
                )
            if lease.status in {TaskLeaseStatus.RELEASED, TaskLeaseStatus.BLOCKED} and fence_token < lease.fence_token:
                return PolicyDecision(
                    allow=False,
                    denials=[
                        f"stale fence token rejected: presented {fence_token} < lease fence_token {lease.fence_token}"
                    ],
                    requires_human=True,
                )
        max_known = max(
            lease.fence_token if lease is not None else 0,
            latest_checkpoint.fence_token if latest_checkpoint is not None else 0,
        )
        if max_known > 0 and fence_token < max_known:
            return PolicyDecision(
                allow=False,
                denials=[f"stale fence token rejected: presented {fence_token} < current {max_known}"],
                requires_human=True,
            )
        return None

    def _effective_fence_token(
        self,
        *,
        fence_token: int | None,
        lease: TaskLease | None,
        latest_checkpoint: RunCheckpoint | None,
    ) -> int:
        if fence_token is not None and fence_token >= 1:
            return fence_token
        if lease is not None:
            return lease.fence_token
        if latest_checkpoint is not None:
            return latest_checkpoint.fence_token
        return 1

    def _build_replay_result(
        self,
        existing: IdempotencyRecord,
        *,
        current: WorkItem,
        fallback_fence_token: int | None,
    ) -> WorkItemTransitionResult:
        payload = existing.response_payload
        event_data = payload.get("event")
        if isinstance(event_data, dict):
            event = ControlPlaneEvent(**event_data)
        else:
            event = ControlPlaneEvent(
                sequence=len(self.store.list_control_plane_events()),
                client_id=current.assigned_client_id or "control-plane",
                run_id=current.run_id,
                task_id=current.work_item_id,
                kind=ControlPlaneEventKind.WORK_ITEM,
                status=_event_status(current.status),
                actor="idempotency",
                role="operator",
                evidence_digest=current.evidence_digest,
                summary=f"Idempotent replay for work item {current.work_item_id}.",
            )
        decision_data = payload.get("decision")
        decision = (
            PolicyDecision(**decision_data)
            if isinstance(decision_data, dict)
            else PolicyDecision(allow=True, reasons=["idempotent replay"])
        )
        cp_data = payload.get("checkpoint")
        checkpoint = RunCheckpoint(**cp_data) if isinstance(cp_data, dict) else None
        return WorkItemTransitionResult(
            status_code=existing.status_code,
            work_item=current,
            event=event,
            decision=decision,
            fence_token=payload.get("fence_token", fallback_fence_token),
            checkpoint=checkpoint,
            idempotency_replayed=True,
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
        min_fence_token: int | None = None,
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
        fence_token = self.store.next_fence_token(item.work_item_id)
        if min_fence_token is not None:
            fence_token = max(fence_token, min_fence_token)
        if item.status == WorkItemStatus.REQUEUED:
            latest_cp = self.store.latest_checkpoint(item.run_id, work_item_id=item.work_item_id)
            if latest_cp is not None:
                fence_token = max(fence_token, latest_cp.fence_token + 1)

        lease = self.store.acquire_task_lease(
            TaskLeaseAcquireRequest(
                task_id=item.work_item_id,
                run_id=item.run_id,
                client_id=selected.manifest.client_id,
                role=item.role,
                actor=actor,
                fence_token=fence_token,
            )
        )
        assignment = self.work_items.assign(
            item.work_item_id,
            actor=actor,
            role=role,
            client_id=client_id,
            assigned_client_id=selected.manifest.client_id,
            lease_id=lease.task_id,
            fence_token=lease.fence_token,
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
            fence_token=lease.fence_token,
        )

    def assign_next(
        self,
        *,
        run_id: str | None = None,
        actor: str = "scheduler",
        role: str = "operator",
        client_id: str = "control-plane",
    ) -> SchedulerDecisionResult:
        return self.schedule_next(run_id=run_id, actor=actor, role=role, client_id=client_id)

    def assign_work_item(
        self,
        work_item_id: str,
        *,
        actor: str = "scheduler",
        role: str = "operator",
        client_id: str = "control-plane",
        min_fence_token: int | None = None,
    ) -> SchedulerDecisionResult:
        return self.schedule_work_item(
            work_item_id,
            actor=actor,
            role=role,
            client_id=client_id,
            min_fence_token=min_fence_token,
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
