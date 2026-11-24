from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from vad.control_plane.leases import (
    StaleFenceTokenError,
    TaskLease,
    TaskLeaseAcquireRequest,
    TaskLeaseReleaseRequest,
    TaskLeaseRenewRequest,
    TaskLeaseStatus,
    new_task_lease,
)


def _make_request(*, fence_token: int | None = None) -> TaskLeaseAcquireRequest:
    return TaskLeaseAcquireRequest(
        task_id="build",
        run_id="run-42",
        client_id="codex-local",
        role="builder",
        actor="builder-1",
        ttl_seconds=60,
        fence_token=fence_token,
    )


def test_default_fence_token_backward_compatibility():
    now = datetime(2026, 7, 2, 0, 0, tzinfo=timezone.utc)

    direct = TaskLease(
        task_id="build",
        run_id="run-42",
        client_id="codex-local",
        role="builder",
        actor="builder-1",
        acquired_at=now,
        expires_at=now + timedelta(seconds=60),
    )
    assert direct.fence_token == 1

    acquire_req = TaskLeaseAcquireRequest(
        task_id="build",
        run_id="run-42",
        client_id="codex-local",
        role="builder",
        actor="builder-1",
    )
    renew_req = TaskLeaseRenewRequest(client_id="codex-local")
    release_req = TaskLeaseReleaseRequest(client_id="codex-local", reason="done")
    assert acquire_req.fence_token is None
    assert renew_req.fence_token is None
    assert release_req.fence_token is None

    lease = new_task_lease(acquire_req, now=now)
    assert lease.fence_token == 1
    lease.renew(now=now + timedelta(seconds=10), ttl_seconds=120)
    assert lease.expires_at == now + timedelta(seconds=130)
    lease.release(reason="done")
    assert lease.status == TaskLeaseStatus.RELEASED

    with pytest.raises(ValidationError):
        TaskLease(
            task_id="build",
            run_id="run-42",
            client_id="codex-local",
            role="builder",
            actor="builder-1",
            acquired_at=now,
            expires_at=now + timedelta(seconds=60),
            fence_token=0,
        )


def test_monotonic_fence_token_increment_and_reissue():
    now = datetime(2026, 7, 2, 0, 0, tzinfo=timezone.utc)
    lease_v1 = new_task_lease(_make_request(), now=now)
    assert lease_v1.fence_token == 1

    lease_v2 = new_task_lease(_make_request(), now=now + timedelta(seconds=5), previous_fence_token=lease_v1.fence_token)
    assert lease_v2.fence_token == 2

    reissued = lease_v2.reissue_with_next_fence(
        client_id="claude-local",
        actor="builder-2",
        now=now + timedelta(seconds=10),
        ttl_seconds=90,
    )
    assert reissued.task_id == lease_v2.task_id
    assert reissued.run_id == lease_v2.run_id
    assert reissued.role == lease_v2.role
    assert reissued.client_id == "claude-local"
    assert reissued.actor == "builder-2"
    assert reissued.status == TaskLeaseStatus.ACTIVE
    assert reissued.fence_token == 3
    assert reissued.expires_at == now + timedelta(seconds=100)

    reissued_new_role = reissued.reissue_with_next_fence(
        client_id="verifier-local",
        actor="verifier-1",
        role="verifier",
        now=now + timedelta(seconds=15),
    )
    assert reissued_new_role.role == "verifier"
    assert reissued_new_role.fence_token == 4


def test_assert_fence_valid_renew_and_release_with_matching_and_mismatched_tokens():
    now = datetime(2026, 7, 2, 0, 0, tzinfo=timezone.utc)
    lease = new_task_lease(_make_request(fence_token=3), now=now, previous_fence_token=2)
    assert lease.fence_token == 3

    lease.assert_fence_valid(3)

    with pytest.raises(StaleFenceTokenError, match="stale"):
        lease.assert_fence_valid(2)

    with pytest.raises(StaleFenceTokenError, match="invalid"):
        lease.assert_fence_valid(4)

    with pytest.raises(StaleFenceTokenError, match="stale"):
        lease.renew(now=now + timedelta(seconds=10), ttl_seconds=60, fence_token=1)
    assert lease.expires_at == now + timedelta(seconds=60)

    with pytest.raises(StaleFenceTokenError, match="invalid"):
        lease.renew(now=now + timedelta(seconds=10), ttl_seconds=60, fence_token=5)

    lease.renew(now=now + timedelta(seconds=10), ttl_seconds=60, fence_token=3)
    assert lease.expires_at == now + timedelta(seconds=70)

    with pytest.raises(StaleFenceTokenError, match="stale"):
        lease.release(reason="zombie", fence_token=2)
    assert lease.status == TaskLeaseStatus.ACTIVE

    with pytest.raises(StaleFenceTokenError, match="invalid"):
        lease.release(reason="future", fence_token=99)
    assert lease.status == TaskLeaseStatus.ACTIVE

    lease.release(reason="completed", fence_token=3)
    assert lease.status == TaskLeaseStatus.RELEASED
    assert lease.release_reason == "completed"


def test_new_task_lease_rejects_non_monotonic_explicit_fence_token():
    now = datetime(2026, 7, 2, 0, 0, tzinfo=timezone.utc)

    with pytest.raises(StaleFenceTokenError):
        new_task_lease(_make_request(fence_token=2), now=now, previous_fence_token=2)

    with pytest.raises(StaleFenceTokenError):
        new_task_lease(_make_request(fence_token=1), now=now, previous_fence_token=3)

    valid = new_task_lease(_make_request(fence_token=5), now=now, previous_fence_token=3)
    assert valid.fence_token == 5
