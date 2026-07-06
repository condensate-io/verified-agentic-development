from __future__ import annotations

from dataclasses import dataclass

from pydantic import ValidationError

from vad.control_plane.events import ControlPlaneEvent, ControlPlaneEventKind, ControlPlaneEventStatus
from vad.policy.decisions import PolicyDecision
from vad.server.db.store import ServerStore


PRIVILEGED_EVENT_ROLES: dict[ControlPlaneEventKind, set[str]] = {
    ControlPlaneEventKind.FILE_CHANGE_APPLIED: {"builder", "release_guardian"},
    ControlPlaneEventKind.APPROVAL_RECORDED: {"release_guardian"},
    ControlPlaneEventKind.SIGNER_EVENT: {"release_guardian"},
    ControlPlaneEventKind.DEPLOYMENT_EVENT: {"release_guardian"},
    ControlPlaneEventKind.RECOVERY_ACTION: {"operator", "release_guardian"},
}


@dataclass(frozen=True)
class EventIngestionResult:
    status_code: int
    event: ControlPlaneEvent
    decision: PolicyDecision


class ControlPlaneEventService:
    def __init__(self, store: ServerStore):
        self.store = store

    def ingest(self, payload: dict) -> EventIngestionResult:
        event = ControlPlaneEvent(**payload)
        decision = self._policy_decision(event)
        if decision.allow:
            self.store.append_control_plane_event(event)
            return EventIngestionResult(status_code=201, event=event, decision=decision)

        denial = ControlPlaneEvent(
            sequence=event.sequence,
            client_id=event.client_id,
            run_id=event.run_id,
            task_id=event.task_id,
            kind=ControlPlaneEventKind.POLICY_DENIED,
            status=ControlPlaneEventStatus.BLOCKED,
            actor=event.actor,
            role=event.role,
            evidence_digest=event.evidence_digest,
            summary=f"Policy denied {event.kind.value}: {'; '.join(decision.denials)}",
        )
        self.store.append_control_plane_event(denial)
        return EventIngestionResult(status_code=403, event=denial, decision=decision)

    def _policy_decision(self, event: ControlPlaneEvent) -> PolicyDecision:
        allowed_roles = PRIVILEGED_EVENT_ROLES.get(event.kind)
        if allowed_roles is None:
            return PolicyDecision(allow=True, reasons=["event ingestion policy satisfied"])
        if event.role in allowed_roles:
            return PolicyDecision(allow=True, reasons=[f"{event.kind.value} role policy satisfied"])
        return PolicyDecision(
            allow=False,
            denials=[f"{event.kind.value} requires one of: {', '.join(sorted(allowed_roles))}"],
            requires_human=True,
        )


__all__ = [
    "ControlPlaneEventService",
    "EventIngestionResult",
    "PRIVILEGED_EVENT_ROLES",
    "ValidationError",
]
