from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from vad.control_plane.leases import TaskLeaseAcquireRequest, TaskLeaseStatus, new_task_lease


def test_task_lease_acquire_renew_release_and_expire():
    now = datetime(2026, 7, 2, 0, 0, tzinfo=timezone.utc)
    lease = new_task_lease(
        TaskLeaseAcquireRequest(
            task_id="build",
            run_id="run-1",
            client_id="codex-local",
            role="builder",
            actor="builder",
            ttl_seconds=30,
        ),
        now=now,
    )

    assert lease.status == TaskLeaseStatus.ACTIVE
    assert lease.expires_at == now + timedelta(seconds=30)
    lease.renew(now=now + timedelta(seconds=10), ttl_seconds=60)
    assert lease.expires_at == now + timedelta(seconds=70)
    lease.release(reason="complete")
    assert lease.status == TaskLeaseStatus.RELEASED
    assert lease.release_reason == "complete"

    expiring = new_task_lease(
        TaskLeaseAcquireRequest(
            task_id="verify",
            run_id="run-1",
            client_id="claude-local",
            role="verifier",
            actor="verifier",
            ttl_seconds=1,
        ),
        now=now,
    )
    expiring.expire(now=now + timedelta(seconds=2))
    assert expiring.status == TaskLeaseStatus.EXPIRED


def test_task_lease_rejects_unsafe_identifiers_and_self_approval():
    with pytest.raises(ValidationError, match="lease identifiers"):
        TaskLeaseAcquireRequest(
            task_id="../escape",
            run_id="run-1",
            client_id="codex-local",
            role="builder",
            actor="builder",
        )

    lease = new_task_lease(
        TaskLeaseAcquireRequest(
            task_id="build",
            run_id="run-1",
            client_id="codex-local",
            role="builder",
            actor="builder",
        )
    )

    with pytest.raises(ValueError, match="builder cannot approve own work"):
        lease.assert_approval_transition_allowed(actor="builder", role="release_guardian")
    with pytest.raises(ValueError, match="release_guardian"):
        lease.assert_approval_transition_allowed(actor="guardian", role="builder")

    lease.assert_approval_transition_allowed(actor="guardian", role="release_guardian")
