from datetime import datetime, timedelta, timezone

import pytest

from vad.control_plane.lifecycle import (
    ControlPlaneLifecycle,
    ControlPlaneLock,
    ControlPlaneState,
    read_lock,
    recover_stale_lock,
    write_lock,
)


def test_control_plane_lifecycle_records_start_ready_and_shutdown_events():
    lifecycle = ControlPlaneLifecycle.start()

    ready = lifecycle.mark_ready()
    draining = lifecycle.request_shutdown(actor="operator")
    stopped = lifecycle.stop()

    assert lifecycle.state == ControlPlaneState.STOPPED
    assert [event.to_state for event in lifecycle.events] == [
        ControlPlaneState.STARTING,
        ControlPlaneState.READY,
        ControlPlaneState.DRAINING,
        ControlPlaneState.STOPPED,
    ]
    assert ready.from_state == ControlPlaneState.STARTING
    assert draining.actor == "operator"
    assert stopped.reason == "control plane stopped"


def test_control_plane_lifecycle_rejects_invalid_transitions():
    lifecycle = ControlPlaneLifecycle.start()

    with pytest.raises(ValueError, match="starting -> draining"):
        lifecycle.request_shutdown()

    lifecycle.mark_ready()
    lifecycle.request_shutdown()
    lifecycle.stop()
    with pytest.raises(ValueError, match="stopped -> failed"):
        lifecycle.fail("late failure")


def test_control_plane_failure_is_terminal_and_evidence_shaped():
    lifecycle = ControlPlaneLifecycle.start()

    event = lifecycle.fail("port bind failed")

    assert lifecycle.state == ControlPlaneState.FAILED
    assert event.from_state == ControlPlaneState.STARTING
    assert event.to_state == ControlPlaneState.FAILED
    assert event.reason == "port bind failed"
    with pytest.raises(ValueError, match="failed -> ready"):
        lifecycle.mark_ready()


def test_control_plane_lock_round_trips(tmp_path):
    heartbeat = datetime(2026, 7, 2, 0, 0, tzinfo=timezone.utc)
    lock = ControlPlaneLock(process_id=123, state=ControlPlaneState.READY, heartbeat_at=heartbeat)
    lock_path = tmp_path / "control-plane.lock"

    write_lock(lock_path, lock)
    loaded = read_lock(lock_path)

    assert loaded.process_id == 123
    assert loaded.state == ControlPlaneState.READY
    assert loaded.heartbeat_at == heartbeat


def test_stale_lock_recovery_removes_old_lock_and_records_event(tmp_path):
    heartbeat = datetime(2026, 7, 2, 0, 0, tzinfo=timezone.utc)
    lock_path = tmp_path / "control-plane.lock"
    write_lock(lock_path, ControlPlaneLock(process_id=123, state=ControlPlaneState.READY, heartbeat_at=heartbeat))

    result = recover_stale_lock(lock_path, now=heartbeat + timedelta(seconds=120), stale_after_seconds=60)

    assert result.recovered is True
    assert result.reason == "stale lock recovered"
    assert result.event is not None
    assert result.event.actor == "recovery"
    assert result.event.from_state == ControlPlaneState.READY
    assert result.event.to_state == ControlPlaneState.STOPPED
    assert not lock_path.exists()


def test_fresh_lock_recovery_keeps_lock(tmp_path):
    heartbeat = datetime(2026, 7, 2, 0, 0, tzinfo=timezone.utc)
    lock_path = tmp_path / "control-plane.lock"
    write_lock(lock_path, ControlPlaneLock(process_id=123, state=ControlPlaneState.READY, heartbeat_at=heartbeat))

    result = recover_stale_lock(lock_path, now=heartbeat + timedelta(seconds=10), stale_after_seconds=60)

    assert result.recovered is False
    assert result.reason == "lock heartbeat is still fresh"
    assert lock_path.exists()
