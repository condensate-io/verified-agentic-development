import sqlite3

import pytest

from vad.control_plane.events import ControlPlaneEvent
from vad.server.db.store import SCHEMA_VERSION, ServerStore


def event(event_id: str, sequence: int, *, run_id: str = "run-1", client_id: str = "codex") -> ControlPlaneEvent:
    return ControlPlaneEvent(
        event_id=event_id,
        sequence=sequence,
        client_id=client_id,
        run_id=run_id,
        task_id="task-1",
        kind="heartbeat",
        status="active",
        actor="builder",
        role="builder",
        summary=f"event {event_id}",
    )


def test_control_plane_events_append_and_load_in_deterministic_order(tmp_path):
    store = ServerStore(tmp_path / "vad.sqlite3")

    store.append_control_plane_event(event("event-2", 2))
    store.append_control_plane_event(event("event-1", 1))
    store.append_control_plane_event(event("event-3", 2))

    loaded = ServerStore(tmp_path / "vad.sqlite3").list_control_plane_events()

    assert [item.event_id for item in loaded] == ["event-1", "event-2", "event-3"]
    assert [item.sequence for item in loaded] == [1, 2, 2]


def test_control_plane_event_duplicate_id_fails_closed(tmp_path):
    store = ServerStore(tmp_path / "vad.sqlite3")
    duplicate = event("event-dup", 1)

    store.append_control_plane_event(duplicate)
    with pytest.raises(sqlite3.IntegrityError):
        store.append_control_plane_event(duplicate)


def test_control_plane_events_filter_by_run_and_client(tmp_path):
    store = ServerStore(tmp_path / "vad.sqlite3")
    store.append_control_plane_event(event("event-a", 1, run_id="run-a", client_id="codex"))
    store.append_control_plane_event(event("event-b", 2, run_id="run-b", client_id="claude"))
    store.append_control_plane_event(event("event-c", 3, run_id="run-a", client_id="claude"))

    assert [item.event_id for item in store.list_control_plane_events(run_id="run-a")] == ["event-a", "event-c"]
    assert [item.event_id for item in store.list_control_plane_events(client_id="claude")] == ["event-b", "event-c"]
    assert [item.event_id for item in store.list_control_plane_events(run_id="run-a", client_id="claude")] == ["event-c"]


def test_control_plane_event_migration_creates_versioned_table_and_indexes(tmp_path):
    db_path = tmp_path / "vad.sqlite3"

    ServerStore(db_path).migrate()

    with sqlite3.connect(db_path) as conn:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        indexes = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'index'")}

    assert version == SCHEMA_VERSION
    assert "control_plane_events" in tables
    assert "idx_control_plane_events_order" in indexes
    assert "idx_control_plane_events_run" in indexes
    assert "idx_control_plane_events_client" in indexes
