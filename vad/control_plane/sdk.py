from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from vad.control_plane.clients import ClientHeartbeat, ClientManifest
from vad.control_plane.events import ControlPlaneEvent
from vad.control_plane.work_items import WorkItem, WorkItemStatus
from vad.server.api.clients import ClientHeartbeatResult, ClientRegistrationResult, ClientRegistryService
from vad.server.api.events import ControlPlaneEventService, EventIngestionResult
from vad.server.api.work_items import WorkItemSchedulerService, WorkItemService, WorkItemTransitionResult
from vad.server.db.store import ServerStore


@dataclass(frozen=True)
class LocalControlPlaneClient:
    store: ServerStore

    @classmethod
    def from_db_path(cls, db_path: str | Path) -> "LocalControlPlaneClient":
        return cls(ServerStore(Path(db_path)))

    def register(self, manifest: ClientManifest) -> ClientRegistrationResult:
        return ClientRegistryService(self.store).register(manifest)

    def heartbeat(self, heartbeat: ClientHeartbeat) -> ClientHeartbeatResult:
        return ClientRegistryService(self.store).heartbeat(heartbeat)

    def emit_event(self, event: ControlPlaneEvent | dict) -> EventIngestionResult:
        payload = event.model_dump(mode="json") if isinstance(event, ControlPlaneEvent) else event
        return ControlPlaneEventService(self.store).ingest(payload)

    def poll_assigned_work(
        self,
        client_id: str,
        *,
        run_id: str | None = None,
    ) -> list[WorkItem]:
        return [
            item
            for item in self.store.list_work_items(run_id=run_id, assigned_client_id=client_id)
            if item.status in {
                WorkItemStatus.ASSIGNED,
                WorkItemStatus.RUNNING,
                WorkItemStatus.BLOCKED,
                WorkItemStatus.WAITING_FOR_HUMAN,
                WorkItemStatus.VERIFYING,
            }
        ]

    def receive_next_work(
        self,
        *,
        run_id: str | None = None,
        actor: str = "connector",
        role: str = "operator",
        client_id: str = "control-plane",
    ):
        return WorkItemSchedulerService(self.store).schedule_next(
            run_id=run_id,
            actor=actor,
            role=role,
            client_id=client_id,
        )

    def accept_work(
        self,
        work_item_id: str,
        *,
        client_id: str,
        actor: str,
        role: str,
    ) -> WorkItemTransitionResult:
        return WorkItemService(self.store).start(
            work_item_id,
            actor=actor,
            role=role,
            client_id=client_id,
        )

    def reject_work(
        self,
        work_item_id: str,
        *,
        client_id: str,
        actor: str,
        role: str,
        reason: str | None = None,
    ) -> WorkItemTransitionResult:
        summary = f"Connector {client_id} rejected work item {work_item_id}."
        if reason:
            summary = f"{summary} Reason: {reason}."
        return WorkItemService(self.store).requeue(
            work_item_id,
            actor=actor,
            role=role,
            client_id=client_id,
            summary=summary,
        )

    def block_work(
        self,
        work_item_id: str,
        *,
        client_id: str,
        actor: str,
        role: str,
        reason: str,
    ) -> WorkItemTransitionResult:
        return WorkItemService(self.store).block(
            work_item_id,
            actor=actor,
            role=role,
            client_id=client_id,
            reason=reason,
        )

    def complete_work(
        self,
        work_item_id: str,
        *,
        client_id: str,
        actor: str,
        role: str,
        evidence_digest: str | None = None,
    ) -> WorkItemTransitionResult:
        return WorkItemService(self.store).complete(
            work_item_id,
            actor=actor,
            role=role,
            client_id=client_id,
            evidence_digest=evidence_digest,
        )

    def fail_work(
        self,
        work_item_id: str,
        *,
        client_id: str,
        actor: str,
        role: str,
    ) -> WorkItemTransitionResult:
        return WorkItemService(self.store).fail(
            work_item_id,
            actor=actor,
            role=role,
            client_id=client_id,
        )
