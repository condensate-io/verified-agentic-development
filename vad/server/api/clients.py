from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from vad.control_plane.clients import ClientHeartbeat, ClientManifest, ClientRuntimeStatus, ClientStatusSnapshot
from vad.control_plane.events import ControlPlaneEvent, ControlPlaneEventKind, ControlPlaneEventStatus
from vad.control_plane.work_items import WorkItemStatus
from vad.server.api.work_items import WorkItemSchedulerService, WorkItemService, WorkItemTransitionResult, SchedulerDecisionResult
from vad.server.db.store import ServerStore


@dataclass(frozen=True)
class ClientRegistrationResult:
    manifest: ClientManifest
    event: ControlPlaneEvent


@dataclass(frozen=True)
class ClientHeartbeatResult:
    heartbeat: ClientHeartbeat
    snapshot: ClientStatusSnapshot
    event: ControlPlaneEvent


@dataclass(frozen=True)
class ClientStaleRecoveryResult:
    snapshot: ClientStatusSnapshot
    event: ControlPlaneEvent
    recovered_work_items: list[WorkItemTransitionResult]
    recovery_events: list[ControlPlaneEvent]
    reassigned_work_items: list[SchedulerDecisionResult]


RECOVERABLE_STALE_WORK_STATUSES = {
    WorkItemStatus.ASSIGNED,
    WorkItemStatus.RUNNING,
    WorkItemStatus.BLOCKED,
    WorkItemStatus.WAITING_FOR_HUMAN,
    WorkItemStatus.VERIFYING,
}


class ClientRegistryService:
    def __init__(self, store: ServerStore):
        self.store = store

    def register(self, manifest: ClientManifest) -> ClientRegistrationResult:
        saved = self.store.register_client_manifest(manifest)
        event = self._append_registry_event(saved, "registered")
        return ClientRegistrationResult(manifest=saved, event=event)

    def unregister(self, client_id: str) -> ClientRegistrationResult:
        removed = self.store.unregister_client_manifest(client_id)
        event = self._append_registry_event(removed, "unregistered")
        return ClientRegistrationResult(manifest=removed, event=event)

    def heartbeat(self, heartbeat: ClientHeartbeat) -> ClientHeartbeatResult:
        saved = self.store.record_client_heartbeat(heartbeat)
        manifest = self.store.load_client_manifest(saved.client_id)
        event = ControlPlaneEvent(
            sequence=len(self.store.list_control_plane_events()) + 1,
            client_id=saved.client_id,
            client_label=manifest.display_name,
            run_id=saved.run_id,
            task_id=saved.task_id,
            kind=ControlPlaneEventKind.HEARTBEAT,
            status=ControlPlaneEventStatus.ACTIVE,
            actor=saved.actor,
            role=saved.role,
            summary=saved.summary,
            created_at=saved.observed_at,
        )
        self.store.append_control_plane_event(event)
        snapshot = ClientStatusSnapshot(
            manifest=manifest,
            status=saved.status,
            last_heartbeat_at=saved.observed_at,
            last_run_id=saved.run_id,
            last_task_id=saved.task_id,
        )
        return ClientHeartbeatResult(heartbeat=saved, snapshot=snapshot, event=event)

    def list_statuses(self) -> list[ClientStatusSnapshot]:
        return self.store.list_client_statuses()

    def mark_stale_clients(
        self,
        *,
        now: datetime | None = None,
        stale_after_seconds: int = 120,
    ) -> list[ClientStatusSnapshot]:
        return [
            result.snapshot
            for result in self.mark_stale_clients_with_recovery(
                now=now,
                stale_after_seconds=stale_after_seconds,
            )
        ]

    def mark_stale_clients_with_recovery(
        self,
        *,
        now: datetime | None = None,
        stale_after_seconds: int = 120,
        auto_reassign: bool = False,
    ) -> list[ClientStaleRecoveryResult]:
        reference = now or datetime.now(timezone.utc)
        stale_snapshots: list[tuple[ClientStatusSnapshot, float]] = []
        for snapshot in self.store.list_client_statuses():
            if snapshot.status == ClientRuntimeStatus.DISCONNECTED or snapshot.last_heartbeat_at is None:
                continue
            heartbeat_at = snapshot.last_heartbeat_at
            if heartbeat_at.tzinfo is None:
                heartbeat_at = heartbeat_at.replace(tzinfo=timezone.utc)
            age = (reference - heartbeat_at.astimezone(timezone.utc)).total_seconds()
            if age <= stale_after_seconds:
                continue
            stale_snapshots.append((snapshot, age))

        marked_results: list[ClientStaleRecoveryResult] = []
        recovered_by_work_item: dict[str, WorkItemTransitionResult] = {}
        recovery_events: list[ControlPlaneEvent] = []

        for snapshot, age in stale_snapshots:
            marked = self.store.mark_client_stale(snapshot.manifest.client_id)
            stale_event = self._append_stale_event(marked, age)
            recovered, item_recovery_events = self._recover_stale_work_items(marked)
            for transition in recovered:
                recovered_by_work_item[transition.work_item.work_item_id] = transition
            recovery_events.extend(item_recovery_events)
            marked_results.append(ClientStaleRecoveryResult(
                snapshot=marked,
                event=stale_event,
                recovered_work_items=recovered,
                recovery_events=item_recovery_events,
                reassigned_work_items=[],
            ))

        reassigned_by_work_item: dict[str, SchedulerDecisionResult] = {}
        if auto_reassign:
            scheduler = WorkItemSchedulerService(self.store)
            for work_item_id in sorted(recovered_by_work_item):
                transition = recovered_by_work_item[work_item_id]
                if transition.status_code != 200:
                    continue
                decision = scheduler.schedule_work_item(
                    work_item_id,
                    actor="recovery-scheduler",
                    role="operator",
                    client_id="control-plane",
                )
                if decision.status_code == 200:
                    reassigned_by_work_item[work_item_id] = decision

        if reassigned_by_work_item:
            combined = list(reassigned_by_work_item.values())
            return [
                ClientStaleRecoveryResult(
                    snapshot=result.snapshot,
                    event=result.event,
                    recovered_work_items=result.recovered_work_items,
                    recovery_events=result.recovery_events,
                    reassigned_work_items=combined if index == 0 else [],
                )
                for index, result in enumerate(marked_results)
            ]
        return marked_results

    def _append_registry_event(self, manifest: ClientManifest, action: str) -> ControlPlaneEvent:
        event = ControlPlaneEvent(
            sequence=len(self.store.list_control_plane_events()) + 1,
            client_id=manifest.client_id,
            client_label=manifest.display_name,
            kind=ControlPlaneEventKind.MESSAGE,
            status=ControlPlaneEventStatus.ACTIVE if action == "registered" else ControlPlaneEventStatus.STALE,
            actor=manifest.client_id,
            role=manifest.trust_state.value,
            summary=f"Client {manifest.display_name} {action}.",
        )
        self.store.append_control_plane_event(event)
        return event

    def _append_stale_event(self, snapshot: ClientStatusSnapshot, age_seconds: float) -> ControlPlaneEvent:
        event = ControlPlaneEvent(
            sequence=len(self.store.list_control_plane_events()) + 1,
            client_id=snapshot.manifest.client_id,
            client_label=snapshot.manifest.display_name,
            run_id=snapshot.last_run_id,
            task_id=snapshot.last_task_id,
            kind=ControlPlaneEventKind.HEARTBEAT,
            status=ControlPlaneEventStatus.STALE,
            actor=snapshot.manifest.client_id,
            role=snapshot.manifest.trust_state.value,
            summary=(
                f"Client {snapshot.manifest.display_name} marked stale after {int(age_seconds)} seconds; "
                f"task leases released: {len(snapshot.lost_task_leases)}."
            ),
        )
        self.store.append_control_plane_event(event)
        return event

    def _recover_stale_work_items(
        self,
        snapshot: ClientStatusSnapshot,
    ) -> tuple[list[WorkItemTransitionResult], list[ControlPlaneEvent]]:
        service = WorkItemService(self.store)
        recovered: list[WorkItemTransitionResult] = []
        recovery_events: list[ControlPlaneEvent] = []
        for item in self.store.list_work_items(assigned_client_id=snapshot.manifest.client_id):
            if item.status not in RECOVERABLE_STALE_WORK_STATUSES:
                continue
            if item.status == WorkItemStatus.REQUEUED:
                continue
            previous_status = item.status.value
            result = service.requeue(
                item.work_item_id,
                actor=snapshot.manifest.client_id,
                role=snapshot.manifest.trust_state.value,
                client_id=snapshot.manifest.client_id,
                summary=(
                    f"Work item {item.work_item_id} requeued after stale client "
                    f"{snapshot.manifest.display_name} abandoned {previous_status} work."
                ),
            )
            if result.status_code != 200:
                continue
            recovered.append(result)
            recovery_event = ControlPlaneEvent(
                sequence=len(self.store.list_control_plane_events()) + 1,
                client_id=snapshot.manifest.client_id,
                client_label=snapshot.manifest.display_name,
                run_id=result.work_item.run_id,
                task_id=result.work_item.work_item_id,
                kind=ControlPlaneEventKind.RECOVERY_ACTION,
                status=ControlPlaneEventStatus.ACTIVE,
                actor=snapshot.manifest.client_id,
                role=snapshot.manifest.trust_state.value,
                evidence_digest=result.work_item.evidence_digest,
                summary=(
                    f"Recovery requeued work item {result.work_item.work_item_id} "
                    f"from stale client {snapshot.manifest.display_name}."
                ),
            )
            self.store.append_control_plane_event(recovery_event)
            recovery_events.append(recovery_event)
        return recovered, recovery_events
