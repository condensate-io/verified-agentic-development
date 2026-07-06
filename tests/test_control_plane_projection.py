from datetime import datetime, timedelta, timezone

from vad.control_plane.events import ControlPlaneEvent
from vad.control_plane.projection import build_dashboard_projection


def event(**overrides):
    payload = {
        "event_id": "event-1",
        "sequence": 1,
        "created_at": datetime(2026, 7, 2, 0, 0, tzinfo=timezone.utc),
        "client_id": "codex-local",
        "run_id": "run-1",
        "task_id": "task-1",
        "kind": "heartbeat",
        "status": "active",
        "actor": "codex",
        "role": "builder",
        "summary": "Heartbeat.",
    }
    payload.update(overrides)
    return ControlPlaneEvent(**payload)


def test_projection_rebuilds_counts_activity_task_board_and_plugins():
    now = datetime(2026, 7, 2, 0, 5, tzinfo=timezone.utc)
    events = [
        event(
            event_id="event-3",
            sequence=3,
            client_id="claude-local",
            task_id="task-2",
            kind="proof_finished",
            status="passed",
            actor="claude",
            summary="Proof finished.",
        ),
        event(
            event_id="event-1",
            sequence=1,
            client_id="codex-local",
            task_id="task-1",
            kind="heartbeat",
            status="active",
            actor="codex",
            summary="Codex heartbeat.",
        ),
        event(
            event_id="event-2",
            sequence=2,
            client_id="codex-local",
            task_id="task-1",
            kind="tool_call_finished",
            status="failed",
            actor="codex",
            summary="Tool failed.",
        ),
    ]

    projection = build_dashboard_projection(events, now=now, stale_after_seconds=600)

    assert [item["event_id"] for item in projection["activity"]] == ["event-1", "event-2", "event-3"]
    assert [item["event_id"] for item in projection["event_timeline"]] == ["event-1", "event-2", "event-3"]
    assert projection["status_counts"] == {"active": 1, "failed": 1, "passed": 1}
    assert projection["client_counts"] == {"claude-local": 1, "codex-local": 2}
    assert projection["task_board"] == [
        {
            "task_id": "task-1",
            "run_id": "run-1",
            "client": "codex-local",
            "client_id": "codex-local",
            "actor": "codex",
            "role": "builder",
            "status": "failed",
            "kind": "tool_call_finished",
            "summary": "Tool failed.",
            "updated_at": "2026-07-02T00:00:00+00:00",
            "event_id": "event-2",
        },
        {
            "task_id": "task-2",
            "run_id": "run-1",
            "client": "claude-local",
            "client_id": "claude-local",
            "actor": "claude",
            "role": "builder",
            "status": "passed",
            "kind": "proof_finished",
            "summary": "Proof finished.",
            "updated_at": "2026-07-02T00:00:00+00:00",
            "event_id": "event-3",
        },
    ]
    assert projection["plugin_status"] == [
        {
            "client_id": "codex-local",
            "connection_id": "codex-local",
            "status": "active",
            "last_seen_at": "2026-07-02T00:00:00+00:00",
            "last_event_id": "event-1",
            "role": "builder",
            "actor": "codex",
        },
    ]
    assert projection["stale_clients"] == []


def test_projection_marks_stale_clients_deterministically():
    seen_at = datetime(2026, 7, 2, 0, 0, tzinfo=timezone.utc)
    now = seen_at + timedelta(seconds=121)

    projection = build_dashboard_projection([event(created_at=seen_at)], now=now, stale_after_seconds=120)

    assert projection["plugin_status"][0]["status"] == "stale"
    assert projection["stale_clients"] == ["codex-local"]
