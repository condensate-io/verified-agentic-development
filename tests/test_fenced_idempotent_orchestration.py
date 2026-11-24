from __future__ import annotations

from datetime import datetime, timedelta, timezone

from vad.control_plane.checkpoints import CheckpointStage
from vad.control_plane.clients import ClientHeartbeat, ClientManifest, ClientRuntimeStatus
from vad.control_plane.events import ControlPlaneEventKind
from vad.control_plane.leases import TaskLeaseStatus
from vad.control_plane.work_items import WorkItem, WorkItemStatus
from vad.server.api.clients import ClientRegistryService
from vad.server.api.work_items import WorkItemSchedulerService, WorkItemService
from vad.server.db.store import ServerStore


def _register_active_client(
    registry: ClientRegistryService,
    tmp_path,
    client_id: str,
    *,
    display_name: str,
    observed_at: datetime,
) -> None:
    registry.register(
        ClientManifest(
            client_id=client_id,
            display_name=display_name,
            client_type="codex",
            version="1.0.0",
            connection_mode="mcp",
            supported_capabilities=("repo_patch",),
            workspace_root=tmp_path,
            trust_state="trusted",
        )
    )
    registry.heartbeat(
        ClientHeartbeat(
            client_id=client_id,
            actor=client_id,
            role="builder",
            observed_at=observed_at,
        )
    )


def _create_work_item(
    service: WorkItemService,
    work_item_id: str = "wi-1",
    *,
    run_id: str = "run-orch-1",
) -> WorkItem:
    item = WorkItem(
        work_item_id=work_item_id,
        run_id=run_id,
        title=f"Orchestrated {work_item_id}",
        role="builder",
        requested_capability="repo_patch",
        priority=10,
        status=WorkItemStatus.QUEUED,
    )
    res = service.create(item, actor="operator", role="operator")
    assert res.status_code == 201
    return res.work_item


def test_monotonic_fence_token_across_crash_recovery_and_reassignment(tmp_path):
    db_path = tmp_path / "fenced_orch.sqlite3"
    store = ServerStore(db_path)
    registry = ClientRegistryService(store)
    work_items = WorkItemService(store)
    scheduler = WorkItemSchedulerService(store)

    t0 = datetime(2026, 11, 24, 1, 0, tzinfo=timezone.utc)
    _register_active_client(registry, tmp_path, "alpha-client", display_name="Alpha", observed_at=t0)
    _create_work_item(work_items, "wi-fenced", run_id="run-fence")

    # Initial assignment acquires lease with fence_token == 1
    first = scheduler.schedule_next(actor="scheduler", role="operator")
    assert first.status_code == 200
    assert first.selected_client_id == "alpha-client"
    assert first.fence_token == 1
    assert first.work_item is not None
    assert first.work_item.lease_id == "wi-fenced"

    lease_1 = store.load_task_lease("wi-fenced")
    assert lease_1.fence_token == 1
    assert lease_1.status == TaskLeaseStatus.ACTIVE

    # Alpha starts work using fence_token=1
    started = work_items.start(
        "wi-fenced",
        actor="alpha-builder",
        role="builder",
        client_id="alpha-client",
        fence_token=1,
    )
    assert started.status_code == 200
    assert started.work_item.status == WorkItemStatus.RUNNING

    # Register healthy Beta client at t0 + 180s; Alpha's heartbeat is now stale (>120s)
    t_recover = t0 + timedelta(seconds=180)
    _register_active_client(registry, tmp_path, "beta-client", display_name="Beta", observed_at=t_recover)

    recovery_results = registry.mark_stale_clients_with_recovery(
        now=t_recover,
        stale_after_seconds=120,
        auto_reassign=True,
    )
    assert len(recovery_results) == 1
    assert recovery_results[0].snapshot.manifest.client_id == "alpha-client"
    assert recovery_results[0].snapshot.status == ClientRuntimeStatus.STALE
    assert recovery_results[0].recovered_work_items[0].work_item.status == WorkItemStatus.REQUEUED
    assert "expired fence_token=1" in recovery_results[0].recovery_events[0].summary
    assert len(recovery_results[0].reassigned_work_items) == 1

    reassigned = recovery_results[0].reassigned_work_items[0]
    assert reassigned.selected_client_id == "beta-client"
    assert reassigned.fence_token == 2
    assert reassigned.work_item is not None
    assert reassigned.work_item.status == WorkItemStatus.ASSIGNED
    assert reassigned.work_item.assigned_client_id == "beta-client"

    lease_2 = store.load_task_lease("wi-fenced")
    assert lease_2.fence_token == 2
    assert lease_2.client_id == "beta-client"
    assert lease_2.status == TaskLeaseStatus.ACTIVE

    # Beta completes work with fence_token=2
    assert work_items.start("wi-fenced", actor="beta", role="builder", client_id="beta-client", fence_token=2).status_code == 200
    assert work_items.verify("wi-fenced", actor="verifier", role="verifier", client_id="beta-client", fence_token=2).status_code == 200
    assert work_items.approve("wi-fenced", actor="guardian", role="release_guardian", client_id="beta-client", fence_token=2).status_code == 200
    done = work_items.complete(
        "wi-fenced",
        actor="guardian",
        role="release_guardian",
        client_id="beta-client",
        evidence_digest="a" * 64,
        fence_token=2,
    )
    assert done.status_code == 200
    assert done.work_item.status == WorkItemStatus.COMPLETED


def test_zombie_client_with_stale_fence_token_is_rejected_409_without_mutating_state(tmp_path):
    db_path = tmp_path / "zombie_fence.sqlite3"
    store = ServerStore(db_path)
    registry = ClientRegistryService(store)
    work_items = WorkItemService(store)
    scheduler = WorkItemSchedulerService(store)

    t0 = datetime(2026, 11, 24, 2, 0, tzinfo=timezone.utc)
    _register_active_client(registry, tmp_path, "zombie-client", display_name="Zombie", observed_at=t0)
    _create_work_item(work_items, "wi-zombie", run_id="run-zombie")

    assigned = scheduler.schedule_work_item("wi-zombie")
    assert assigned.status_code == 200
    assert assigned.fence_token == 1

    # Simulate stale detection BEFORE reassignment: expired lease must still reject zombie's fence_token=1
    t_stale = t0 + timedelta(seconds=300)
    registry.mark_stale_clients_with_recovery(now=t_stale, stale_after_seconds=120, auto_reassign=False)
    assert store.load_work_item("wi-zombie").status == WorkItemStatus.REQUEUED
    assert store.load_task_lease("wi-zombie").status == TaskLeaseStatus.EXPIRED

    zombie_before_reassign = work_items.transition(
        "wi-zombie",
        status=WorkItemStatus.CANCELLED,
        actor="zombie-actor",
        role="builder",
        client_id="zombie-client",
        fence_token=1,
    )
    assert zombie_before_reassign.status_code == 409
    assert zombie_before_reassign.decision.allow is False
    assert "stale fence token rejected" in zombie_before_reassign.decision.denials[0]
    assert zombie_before_reassign.event.kind == ControlPlaneEventKind.POLICY_DENIED
    assert store.load_work_item("wi-zombie").status == WorkItemStatus.REQUEUED

    # Register healthy client and reassign -> fence_token becomes 2
    _register_active_client(registry, tmp_path, "healthy-client", display_name="Healthy", observed_at=t_stale)
    reassigned = scheduler.schedule_work_item("wi-zombie")
    assert reassigned.status_code == 200
    assert reassigned.selected_client_id == "healthy-client"
    assert reassigned.fence_token == 2

    # Healthy client moves item to RUNNING with fence_token=2
    running = work_items.start(
        "wi-zombie",
        actor="healthy-builder",
        role="builder",
        client_id="healthy-client",
        fence_token=2,
    )
    assert running.status_code == 200

    # Resurrected zombie tries to write VERIFYING with old fence_token=1 -> 409, state untouched
    zombie_write = work_items.transition(
        "wi-zombie",
        status=WorkItemStatus.VERIFYING,
        actor="zombie-actor",
        role="builder",
        client_id="zombie-client",
        fence_token=1,
    )
    assert zombie_write.status_code == 409
    assert zombie_write.decision.allow is False
    assert "stale fence token rejected" in zombie_write.decision.denials[0]
    assert zombie_write.event.kind == ControlPlaneEventKind.POLICY_DENIED
    assert "stale fence token rejected" in zombie_write.event.summary

    # Future/bogus fence_token=99 is also rejected with 409
    bogus_write = work_items.transition(
        "wi-zombie",
        status=WorkItemStatus.VERIFYING,
        actor="zombie-actor",
        role="builder",
        client_id="zombie-client",
        fence_token=99,
    )
    assert bogus_write.status_code == 409
    assert "stale fence token rejected" in bogus_write.decision.denials[0]

    persisted = store.load_work_item("wi-zombie")
    assert persisted.status == WorkItemStatus.RUNNING
    assert persisted.assigned_client_id == "healthy-client"


def test_idempotency_replay_cleanly_and_conflict_fails_closed_409(tmp_path):
    db_path = tmp_path / "idempotent_orch.sqlite3"
    store = ServerStore(db_path)
    registry = ClientRegistryService(store)
    work_items = WorkItemService(store)
    scheduler = WorkItemSchedulerService(store)

    t0 = datetime(2026, 11, 24, 3, 0, tzinfo=timezone.utc)
    _register_active_client(registry, tmp_path, "worker-1", display_name="Worker 1", observed_at=t0)
    _create_work_item(work_items, "wi-idem", run_id="run-idem")
    scheduled = scheduler.schedule_work_item("wi-idem")
    assert scheduled.fence_token == 1

    # First fenced transition with idempotency_key
    first = work_items.transition(
        "wi-idem",
        status=WorkItemStatus.RUNNING,
        actor="builder-1",
        role="builder",
        client_id="worker-1",
        fence_token=1,
        idempotency_key="idem-start-wi-idem-1",
    )
    assert first.status_code == 200
    assert first.idempotency_replayed is False
    assert first.work_item.status == WorkItemStatus.RUNNING

    event_count_after_first = len(store.list_control_plane_events())

    # Identical retry after reopen must replay cleanly with zero duplicate events
    reopened_store = ServerStore(db_path)
    reopened_work_items = WorkItemService(reopened_store)
    replay = reopened_work_items.transition(
        "wi-idem",
        status=WorkItemStatus.RUNNING,
        actor="builder-1",
        role="builder",
        client_id="worker-1",
        fence_token=1,
        idempotency_key="idem-start-wi-idem-1",
    )
    assert replay.status_code == 200
    assert replay.idempotency_replayed is True
    assert replay.work_item.status == WorkItemStatus.RUNNING
    assert replay.event.event_id == first.event.event_id
    assert len(reopened_store.list_control_plane_events()) == event_count_after_first

    # Same idempotency_key with different payload (e.g. different status) must fail closed 409
    conflict = reopened_work_items.transition(
        "wi-idem",
        status=WorkItemStatus.VERIFYING,
        actor="builder-1",
        role="builder",
        client_id="worker-1",
        fence_token=1,
        idempotency_key="idem-start-wi-idem-1",
    )
    assert conflict.status_code == 409
    assert conflict.decision.allow is False
    assert conflict.decision.denials == ["idempotency key conflict: payload mismatch"]
    assert reopened_store.load_work_item("wi-idem").status == WorkItemStatus.RUNNING

    # Same idempotency_key with different fence_token also triggers 409 conflict
    conflict_fence = reopened_work_items.transition(
        "wi-idem",
        status=WorkItemStatus.RUNNING,
        actor="builder-1",
        role="builder",
        client_id="worker-1",
        fence_token=2,
        idempotency_key="idem-start-wi-idem-1",
    )
    assert conflict_fence.status_code == 409
    assert conflict_fence.decision.denials == ["idempotency key conflict: payload mismatch"]


def test_durable_checkpoints_persist_across_store_reopen_and_recover_resume_stage(tmp_path):
    db_path = tmp_path / "checkpoints_orch.sqlite3"
    store = ServerStore(db_path)
    registry = ClientRegistryService(store)
    work_items = WorkItemService(store)
    scheduler = WorkItemSchedulerService(store)

    t0 = datetime(2026, 11, 24, 4, 0, tzinfo=timezone.utc)
    _register_active_client(registry, tmp_path, "worker-cp", display_name="Worker CP", observed_at=t0)
    item = _create_work_item(work_items, "wi-cp", run_id="run-cp-orch")

    # Before any checkpoint, resume stage is EIP_CREATED
    latest, next_stage = work_items.recover_from_checkpoint("run-cp-orch", "wi-cp")
    assert latest is None
    assert next_stage == CheckpointStage.EIP_CREATED

    # Record initial planning checkpoints
    eip_digest = "1" * 64
    plan_digest = "2" * 64
    diff_digest = "3" * 64
    evidence_digest = "4" * 64

    work_items.record_checkpoint(
        item,
        stage=CheckpointStage.EIP_CREATED,
        actor="planner",
        role="operator",
        artifact_digests={"eip": eip_digest},
    )
    work_items.record_checkpoint(
        item,
        stage=CheckpointStage.PROOF_PLANNED,
        actor="planner",
        role="operator",
        artifact_digests={"eip": eip_digest, "proof_plan": plan_digest},
    )

    # Assign via scheduler -> auto checkpoint CONSTRUCTION_ASSIGNED (fence_token=1)
    scheduled = scheduler.schedule_work_item("wi-cp")
    assert scheduled.fence_token == 1

    # Simulate control-plane restart by reopening ServerStore
    reopened_1 = ServerStore(db_path)
    svc_1 = WorkItemService(reopened_1)
    cp1, resume1 = svc_1.recover_from_checkpoint("run-cp-orch", "wi-cp")
    assert cp1 is not None
    assert cp1.stage == CheckpointStage.CONSTRUCTION_ASSIGNED
    assert cp1.fence_token == 1
    assert resume1 == CheckpointStage.DIFF_PROPOSED

    # Advance to RUNNING -> VERIFYING (auto checkpoint DIFF_PROPOSED)
    assert svc_1.start("wi-cp", actor="worker-cp", role="builder", client_id="worker-cp", fence_token=1).status_code == 200
    verifying = svc_1.transition(
        "wi-cp",
        status=WorkItemStatus.VERIFYING,
        actor="worker-cp",
        role="builder",
        client_id="worker-cp",
        fence_token=1,
        artifact_digests={"diff": diff_digest},
    )
    assert verifying.status_code == 200
    assert verifying.checkpoint is not None
    assert verifying.checkpoint.stage == CheckpointStage.DIFF_PROPOSED
    assert verifying.checkpoint.artifact_digests == {"diff": diff_digest}

    # Simulate another restart before verification/approval
    reopened_2 = ServerStore(db_path)
    svc_2 = WorkItemService(reopened_2)
    cp2, resume2 = svc_2.recover_from_checkpoint("run-cp-orch", "wi-cp")
    assert cp2 is not None
    assert cp2.stage == CheckpointStage.DIFF_PROPOSED
    assert resume2 == CheckpointStage.VERIFIED

    # Approve -> auto checkpoint VERIFIED
    approved = svc_2.approve("wi-cp", actor="verifier-1", role="verifier", client_id="verifier-local", fence_token=1)
    assert approved.status_code == 200
    assert approved.checkpoint is not None
    assert approved.checkpoint.stage == CheckpointStage.VERIFIED

    cp3, resume3 = svc_2.recover_from_checkpoint("run-cp-orch", "wi-cp")
    assert cp3 is not None and cp3.stage == CheckpointStage.VERIFIED
    assert resume3 == CheckpointStage.POLICY_DECIDED

    # Complete -> auto checkpoint RELEASED with evidence digest
    completed = svc_2.complete(
        "wi-cp",
        actor="guardian-1",
        role="release_guardian",
        client_id="guardian-local",
        evidence_digest=evidence_digest,
        fence_token=1,
    )
    assert completed.status_code == 200
    assert completed.checkpoint is not None
    assert completed.checkpoint.stage == CheckpointStage.RELEASED
    assert completed.checkpoint.artifact_digests == {"evidence": evidence_digest}

    reopened_3 = ServerStore(db_path)
    svc_3 = WorkItemService(reopened_3)
    cp4, resume4 = svc_3.recover_from_checkpoint("run-cp-orch", "wi-cp")
    assert cp4 is not None and cp4.stage == CheckpointStage.RELEASED
    assert resume4 == CheckpointStage.MEMORY_SYNCED

    # Final MEMORY_SYNCED checkpoint leaves next_resume_stage as None
    svc_3.record_checkpoint(
        completed.work_item,
        stage=CheckpointStage.MEMORY_SYNCED,
        actor="memory-sync",
        role="operator",
        fence_token=1,
    )
    cp5, resume5 = svc_3.recover_from_checkpoint("run-cp-orch", "wi-cp")
    assert cp5 is not None and cp5.stage == CheckpointStage.MEMORY_SYNCED
    assert resume5 is None
