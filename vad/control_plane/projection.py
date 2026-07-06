from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from vad.control_plane.events import ControlPlaneEvent, ControlPlaneEventKind


def build_dashboard_projection(
    events: list[ControlPlaneEvent],
    *,
    now: datetime | None = None,
    stale_after_seconds: int = 120,
) -> dict[str, Any]:
    ordered = sorted(events, key=lambda event: (event.sequence, _normalized_time(event.created_at), event.event_id))
    reference_time = now or datetime.now(timezone.utc)
    status_counts: dict[str, int] = {}
    client_counts: dict[str, int] = {}
    kind_counts: dict[str, int] = {}
    task_board: dict[str, dict[str, Any]] = {}
    plugins: dict[str, dict[str, Any]] = {}
    last_seen: dict[str, datetime] = {}
    explicit_client_status: dict[str, str] = {}

    activity: list[dict[str, Any]] = []
    for event in ordered:
        status = event.status.value
        client = event.client_label or event.client_id
        kind = event.kind.value
        status_counts[status] = status_counts.get(status, 0) + 1
        client_counts[client] = client_counts.get(client, 0) + 1
        kind_counts[kind] = kind_counts.get(kind, 0) + 1
        created_at = _normalized_time(event.created_at)
        last_seen[client] = max(last_seen.get(client, created_at), created_at)
        if status in {"stale", "disconnected"}:
            explicit_client_status[client] = status

        activity.append({
            "activity_id": event.event_id,
            "event_id": event.event_id,
            "run_id": event.run_id,
            "task_id": event.task_id,
            "kind": kind,
            "status": status,
            "client": client,
            "client_id": event.client_id,
            "actor": event.actor,
            "role": event.role,
            "summary": event.summary,
            "created_at": created_at.isoformat(),
            "evidence_digest": event.evidence_digest,
        })

        if event.task_id:
            task_board[event.task_id] = {
                "task_id": event.task_id,
                "run_id": event.run_id,
                "client": client,
                "client_id": event.client_id,
                "actor": event.actor,
                "role": event.role,
                "status": status,
                "kind": kind,
                "summary": event.summary,
                "updated_at": created_at.isoformat(),
                "event_id": event.event_id,
            }

        if event.kind in {ControlPlaneEventKind.HEARTBEAT, ControlPlaneEventKind.MESSAGE}:
            plugins[client] = {
                "client_id": client,
                "connection_id": event.client_id,
                "status": explicit_client_status.get(client) or _client_status(created_at, reference_time, stale_after_seconds),
                "last_seen_at": created_at.isoformat(),
                "last_event_id": event.event_id,
                "role": event.role,
                "actor": event.actor,
            }

    stale_clients = [
        client
        for client, seen_at in sorted(last_seen.items())
        if explicit_client_status.get(client) == "stale" or _client_status(seen_at, reference_time, stale_after_seconds) == "stale"
    ]
    for client in stale_clients:
        if client in plugins:
            plugins[client]["status"] = "stale"

    return {
        "activity": activity,
        "event_timeline": activity,
        "status_counts": dict(sorted(status_counts.items())),
        "client_counts": dict(sorted(client_counts.items())),
        "kind_counts": dict(sorted(kind_counts.items())),
        "task_board": [task_board[key] for key in sorted(task_board)],
        "plugin_status": [plugins[key] for key in sorted(plugins)],
        "stale_clients": stale_clients,
    }


def _client_status(seen_at: datetime, now: datetime, stale_after_seconds: int) -> str:
    age = (_normalized_time(now) - _normalized_time(seen_at)).total_seconds()
    return "stale" if age > stale_after_seconds else "active"


def _normalized_time(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
