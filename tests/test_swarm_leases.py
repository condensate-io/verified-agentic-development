from datetime import datetime, timedelta

from vad.swarm.leases import TaskLease


def test_missed_heartbeat_marks_task_for_retry_with_evidence():
    now = datetime(2026, 1, 1, 12, 0, 0)
    lease = TaskLease(task_id="build", holder_id="builder", expires_at=now - timedelta(seconds=1), max_retries=2)

    lease.evaluate(now)

    assert lease.retry_count == 1
    assert lease.blocked is False
    assert lease.blocker == "missed heartbeat"
    assert lease.to_evidence()["blocker"] == "missed heartbeat"


def test_retry_limit_blocks_with_evidence():
    now = datetime(2026, 1, 1, 12, 0, 0)
    lease = TaskLease(
        task_id="build",
        holder_id="builder",
        expires_at=now - timedelta(seconds=1),
        retry_count=2,
        max_retries=2,
    )

    lease.evaluate(now)

    assert lease.blocked is True
    assert lease.blocker == "retry limit exceeded"
    assert lease.to_evidence()["blocked"] is True


def test_heartbeat_extends_active_lease():
    now = datetime(2026, 1, 1, 12, 0, 0)
    lease = TaskLease(task_id="build", holder_id="builder", expires_at=now + timedelta(seconds=1))

    lease.heartbeat(now, ttl_seconds=30)

    assert lease.expires_at == now + timedelta(seconds=30)
