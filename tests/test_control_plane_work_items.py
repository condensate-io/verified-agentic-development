import sqlite3
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from vad.control_plane.clients import ClientHeartbeat, ClientManifest
from vad.control_plane.leases import TaskLeaseAcquireRequest, new_task_lease
from vad.control_plane.work_items import WorkItem, WorkItemGovernance, WorkItemStatus, update_work_item
from vad.server.api.work_items import WorkItemSchedulerService, WorkItemService
from vad.server.db.store import SCHEMA_VERSION, ServerStore


def work_item(
    work_item_id: str = "work-1",
    *,
    run_id: str = "run-1",
    priority: int = 100,
    status: WorkItemStatus = WorkItemStatus.QUEUED,
) -> WorkItem:
    return WorkItem(
        work_item_id=work_item_id,
        run_id=run_id,
        title=f"Work {work_item_id}",
        description="Durable orchestrator work item.",
        role="builder",
        requested_capability="repo_patch",
        priority=priority,
        status=status,
        created_at=datetime(2026, 7, 3, 0, 0, min(priority, 59), tzinfo=timezone.utc),
        updated_at=datetime(2026, 7, 3, 0, 0, min(priority, 59), tzinfo=timezone.utc),
    )


def manifest(
    tmp_path,
    client_id: str = "codex-local",
    *,
    display_name: str = "Codex",
    supported_capabilities: tuple[str, ...] = ("repo_patch",),
    trust_state: str = "trusted",
) -> ClientManifest:
    return ClientManifest(
        client_id=client_id,
        display_name=display_name,
        client_type="codex",
        version="1.0.0",
        connection_mode="mcp",
        supported_capabilities=supported_capabilities,
        workspace_root=tmp_path,
        trust_state=trust_state,
    )


def register_active_client(
    store: ServerStore,
    tmp_path,
    client_id: str,
    *,
    display_name: str,
    capabilities: tuple[str, ...] = ("repo_patch",),
    trust_state: str = "trusted",
) -> None:
    store.register_client_manifest(
        manifest(
            tmp_path,
            client_id,
            display_name=display_name,
            supported_capabilities=capabilities,
            trust_state=trust_state,
        )
    )
    store.record_client_heartbeat(ClientHeartbeat(
        client_id=client_id,
        actor=client_id,
        role="builder",
    ))


def test_work_item_model_validates_required_fields_and_safe_identifiers():
    item = work_item()

    assert item.status == WorkItemStatus.QUEUED
    assert item.requested_capability == "repo_patch"
    assert item.priority == 100

    with pytest.raises(ValidationError, match="work item identifiers"):
        WorkItem(
            work_item_id="../escape",
            run_id="run-1",
            title="Unsafe",
            role="builder",
        )

    with pytest.raises(ValidationError, match="Input should be"):
        WorkItem(
            work_item_id="work-bad-status",
            run_id="run-1",
            title="Bad status",
            role="builder",
            status="done",
        )

    with pytest.raises(ValidationError, match="String should match pattern"):
        WorkItem(
            work_item_id="work-bad-digest",
            run_id="run-1",
            title="Bad digest",
            role="builder",
            evidence_digest="not-a-digest",
        )


def test_work_item_governance_validates_effort_token_approval_and_operator_intent():
    governance = WorkItemGovernance(
        effort_type="feature",
        mees_estimate=88,
        token_budget=4000,
        approval_required=True,
        live_service_opt_in=True,
        high_risk=True,
        operator_intent_ref="operator-approved-live-run",
        approval_ref="approval-123",
    )

    assert governance.effort_type == "feature"
    assert governance.mees_estimate == 88
    assert governance.token_budget == 4000
    assert governance.live_service_opt_in is True
    with pytest.raises(ValidationError, match="MEES under 50 requires work-item approval"):
        WorkItemGovernance(effort_type="migration", mees_estimate=40, token_budget=100)
    with pytest.raises(ValidationError, match="high-risk work requires work-item approval"):
        WorkItemGovernance(effort_type="deploy", mees_estimate=80, token_budget=100, high_risk=True)
    with pytest.raises(ValidationError, match="current operator intent reference"):
        WorkItemGovernance(
            effort_type="deploy",
            mees_estimate=80,
            token_budget=100,
            approval_required=True,
            live_service_opt_in=True,
        )


def test_work_items_persist_and_reload_after_store_reopen(tmp_path):
    db_path = tmp_path / "vad.sqlite3"
    item = work_item("work-persist").model_copy(update={
        "governance": WorkItemGovernance(
            effort_type="feature",
            mees_estimate=91,
            token_budget=2000,
            approval_required=False,
        )
    })

    ServerStore(db_path).save_work_item(item)
    loaded = ServerStore(db_path).load_work_item("work-persist")

    assert loaded == item
    assert loaded.work_item_id == "work-persist"
    assert loaded.description == "Durable orchestrator work item."
    assert loaded.governance is not None
    assert loaded.governance.mees_estimate == 91
    assert loaded.governance.token_budget == 2000


def test_work_items_list_in_priority_order_and_filter_by_run_status_and_client(tmp_path):
    db_path = tmp_path / "vad.sqlite3"
    store = ServerStore(db_path)
    store.register_client_manifest(manifest(tmp_path))

    low_priority = work_item("work-low", priority=80, status=WorkItemStatus.QUEUED)
    high_priority = update_work_item(
        work_item("work-high", priority=10, status=WorkItemStatus.ASSIGNED),
        status=WorkItemStatus.ASSIGNED,
        assigned_client_id="codex-local",
        now=datetime(2026, 7, 3, 1, 0, tzinfo=timezone.utc),
    )
    other_run = work_item("work-other", run_id="run-2", priority=5)

    store.save_work_item(low_priority)
    store.save_work_item(high_priority)
    store.save_work_item(other_run)

    assert [item.work_item_id for item in store.list_work_items()] == [
        "work-other",
        "work-high",
        "work-low",
    ]
    assert [item.work_item_id for item in store.list_work_items(run_id="run-1")] == [
        "work-high",
        "work-low",
    ]
    assert [item.work_item_id for item in store.list_work_items(status=WorkItemStatus.QUEUED)] == [
        "work-other",
        "work-low",
    ]
    assert [
        item.work_item_id
        for item in store.list_work_items(assigned_client_id="codex-local")
    ] == ["work-high"]


def test_work_item_update_persists_assignment_lease_status_and_evidence(tmp_path):
    store = ServerStore(tmp_path / "vad.sqlite3")
    store.register_client_manifest(manifest(tmp_path))
    lease = new_task_lease(TaskLeaseAcquireRequest(
        task_id="lease-build",
        run_id="run-1",
        client_id="codex-local",
        role="builder",
        actor="builder",
    ))
    store.save_task_lease(lease)
    store.save_work_item(work_item("work-update"))

    digest = "a" * 64
    updated = update_work_item(
        store.load_work_item("work-update"),
        status=WorkItemStatus.ASSIGNED,
        assigned_client_id="codex-local",
        lease_id="lease-build",
        evidence_digest=digest,
        now=datetime(2026, 7, 3, 2, 0, tzinfo=timezone.utc),
    )
    store.update_work_item(updated)
    loaded = ServerStore(tmp_path / "vad.sqlite3").load_work_item("work-update")

    assert loaded.status == WorkItemStatus.ASSIGNED
    assert loaded.assigned_client_id == "codex-local"
    assert loaded.lease_id == "lease-build"
    assert loaded.evidence_digest == digest
    assert loaded.updated_at == datetime(2026, 7, 3, 2, 0, tzinfo=timezone.utc)


def test_work_item_duplicate_and_missing_update_fail_closed(tmp_path):
    store = ServerStore(tmp_path / "vad.sqlite3")
    item = work_item("work-dup")
    store.save_work_item(item)

    with pytest.raises(sqlite3.IntegrityError):
        store.save_work_item(item)

    with pytest.raises(KeyError):
        store.update_work_item(work_item("missing-work"))


def test_work_item_persistence_does_not_create_dashboard_only_state(tmp_path):
    store = ServerStore(tmp_path / "vad.sqlite3")

    store.save_work_item(work_item("work-dashboard"))

    assert store.list_dashboard_activity() == []
    assert store.has_control_plane_events() is False


def test_work_item_migration_creates_versioned_table_and_indexes(tmp_path):
    db_path = tmp_path / "vad.sqlite3"

    ServerStore(db_path).migrate()

    with sqlite3.connect(db_path) as conn:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        indexes = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'index'")}

    assert version == SCHEMA_VERSION
    assert "work_items" in tables
    assert "idx_work_items_run_status" in indexes
    assert "idx_work_items_status_priority" in indexes
    assert "idx_work_items_assigned_client" in indexes


def test_work_item_schema_contains_orchestrator_fields():
    schema = WorkItem.json_schema()
    properties = schema["properties"]

    for field in [
        "work_item_id",
        "run_id",
        "title",
        "description",
        "role",
        "requested_capability",
        "priority",
        "status",
        "created_at",
        "updated_at",
        "assigned_client_id",
        "lease_id",
        "evidence_digest",
    ]:
        assert field in properties
    assert "work_item_id" in schema["required"]
    assert "run_id" in schema["required"]
    assert "title" in schema["required"]
    assert "role" in schema["required"]


def test_work_item_service_creates_item_and_emits_replayable_event(tmp_path):
    store = ServerStore(tmp_path / "vad.sqlite3")
    service = WorkItemService(store)

    result = service.create(
        work_item("work-created"),
        actor="operator",
        role="operator",
        client_id="operator-local",
    )
    loaded = store.load_work_item("work-created")
    events = store.list_control_plane_events()

    assert result.status_code == 201
    assert result.decision.allow is True
    assert loaded.work_item_id == "work-created"
    assert events[0].kind == "work_item"
    assert events[0].status == "active"
    assert events[0].run_id == "run-1"
    assert events[0].task_id == "work-created"
    assert events[0].actor == "operator"
    assert events[0].role == "operator"


def test_work_item_service_assigns_starts_verifies_approves_and_completes_with_events(tmp_path):
    store = ServerStore(tmp_path / "vad.sqlite3")
    service = WorkItemService(store)
    store.register_client_manifest(manifest(tmp_path))
    lease = new_task_lease(TaskLeaseAcquireRequest(
        task_id="lease-work",
        run_id="run-1",
        client_id="codex-local",
        role="builder",
        actor="builder",
    ))
    store.save_task_lease(lease)
    service.create(work_item("work-lifecycle"), actor="operator", role="operator")

    assigned = service.assign(
        "work-lifecycle",
        actor="operator",
        role="operator",
        client_id="operator-local",
        assigned_client_id="codex-local",
        lease_id="lease-work",
    )
    running = service.start("work-lifecycle", actor="builder", role="builder", client_id="codex-local")
    verifying = service.verify("work-lifecycle", actor="verifier", role="verifier", client_id="vscode-local")
    approved = service.approve("work-lifecycle", actor="guardian", role="release_guardian", client_id="opencode-local")
    completed = service.complete(
        "work-lifecycle",
        actor="guardian",
        role="release_guardian",
        client_id="opencode-local",
        evidence_digest="b" * 64,
    )
    loaded = store.load_work_item("work-lifecycle")
    events = store.list_control_plane_events()

    assert assigned.work_item.assigned_client_id == "codex-local"
    assert assigned.work_item.lease_id == "lease-work"
    assert running.work_item.status == WorkItemStatus.RUNNING
    assert verifying.work_item.status == WorkItemStatus.VERIFYING
    assert approved.work_item.status == WorkItemStatus.APPROVED
    assert completed.work_item.status == WorkItemStatus.COMPLETED
    assert completed.event.status == "passed"
    assert completed.event.evidence_digest == "b" * 64
    assert loaded.status == WorkItemStatus.COMPLETED
    assert loaded.evidence_digest == "b" * 64
    assert [event.kind.value for event in events] == ["work_item"] * 6
    assert [event.sequence for event in events] == [1, 2, 3, 4, 5, 6]
    assert [event.task_id for event in events] == ["work-lifecycle"] * 6


def test_work_item_service_blocks_waits_requeues_fails_and_cancels_explicitly(tmp_path):
    store = ServerStore(tmp_path / "vad.sqlite3")
    service = WorkItemService(store)
    service.create(work_item("work-recovery"), actor="operator", role="operator")

    blocked = service.block(
        "work-recovery",
        actor="builder",
        role="builder",
        client_id="codex-local",
        reason="dependency approval required",
    )
    waiting = service.wait_for_human(
        "work-recovery",
        actor="operator",
        role="operator",
        client_id="operator-local",
    )
    running = service.transition(
        "work-recovery",
        status=WorkItemStatus.RUNNING,
        actor="operator",
        role="operator",
        client_id="operator-local",
    )
    failed = service.fail("work-recovery", actor="verifier", role="verifier", client_id="vscode-local")
    requeued = service.requeue("work-recovery", actor="operator", role="operator", client_id="operator-local")
    cancelled = service.cancel("work-recovery", actor="operator", role="operator", client_id="operator-local")
    events = store.list_control_plane_events()

    assert blocked.work_item.status == WorkItemStatus.BLOCKED
    assert blocked.work_item.blocked_reason == "dependency approval required"
    assert waiting.event.status == "needs_human"
    assert running.work_item.status == WorkItemStatus.RUNNING
    assert failed.event.status == "failed"
    assert requeued.work_item.status == WorkItemStatus.REQUEUED
    assert cancelled.work_item.status == WorkItemStatus.CANCELLED
    assert events[-1].status == "failed"
    assert store.load_work_item("work-recovery").status == WorkItemStatus.CANCELLED


def test_invalid_work_item_transition_fails_closed_and_emits_policy_denial(tmp_path):
    store = ServerStore(tmp_path / "vad.sqlite3")
    service = WorkItemService(store)
    service.create(work_item("work-terminal"), actor="operator", role="operator")
    service.cancel("work-terminal", actor="operator", role="operator", client_id="operator-local")

    denied = service.transition(
        "work-terminal",
        status=WorkItemStatus.QUEUED,
        actor="operator",
        role="operator",
        client_id="operator-local",
    )
    loaded = store.load_work_item("work-terminal")
    events = store.list_control_plane_events()

    assert denied.status_code == 409
    assert denied.decision.allow is False
    assert denied.event.kind == "policy_denied"
    assert denied.event.status == "blocked"
    assert denied.event.task_id == "work-terminal"
    assert "cancelled->queued is not allowed" in denied.decision.denials[0]
    assert loaded.status == WorkItemStatus.CANCELLED
    assert [event.kind.value for event in events] == ["work_item", "work_item", "policy_denied"]


def test_scheduler_assigns_highest_priority_queued_work_to_active_trusted_client(tmp_path):
    store = ServerStore(tmp_path / "vad.sqlite3")
    scheduler = WorkItemSchedulerService(store)
    register_active_client(store, tmp_path, "codex-local", display_name="Codex")
    store.save_work_item(work_item("work-low", priority=90))
    store.save_work_item(work_item("work-high", priority=5))

    result = scheduler.schedule_next(actor="operator", role="operator", client_id="operator-local")
    loaded = store.load_work_item("work-high")
    events = store.list_control_plane_events()

    assert result.status_code == 200
    assert result.decision.allow is True
    assert result.selected_client_id == "codex-local"
    assert result.work_item.work_item_id == "work-high"
    assert loaded.status == WorkItemStatus.ASSIGNED
    assert loaded.assigned_client_id == "codex-local"
    assert events[-1].kind == "work_item"
    assert events[-1].summary == "Scheduler assigned work item work-high to codex-local."
    assert store.load_work_item("work-low").status == WorkItemStatus.QUEUED


def test_scheduler_filters_by_active_trusted_client_and_requested_capability(tmp_path):
    store = ServerStore(tmp_path / "vad.sqlite3")
    scheduler = WorkItemSchedulerService(store)
    register_active_client(
        store,
        tmp_path,
        "observer-local",
        display_name="Observer",
        capabilities=("repo_read",),
    )
    register_active_client(
        store,
        tmp_path,
        "quarantined-local",
        display_name="Quarantined",
        trust_state="quarantined",
    )
    register_active_client(store, tmp_path, "codex-local", display_name="Codex", capabilities=("repo_patch",))
    store.save_work_item(work_item("work-capability", priority=5))

    result = scheduler.schedule_work_item("work-capability")

    assert result.status_code == 200
    assert result.selected_client_id == "codex-local"
    assert store.load_work_item("work-capability").assigned_client_id == "codex-local"


def test_scheduler_prefers_eligible_client_with_fewer_active_leases(tmp_path):
    store = ServerStore(tmp_path / "vad.sqlite3")
    scheduler = WorkItemSchedulerService(store)
    register_active_client(store, tmp_path, "codex-local", display_name="Codex")
    register_active_client(store, tmp_path, "vscode-local", display_name="VS Code")
    store.save_task_lease(new_task_lease(TaskLeaseAcquireRequest(
        task_id="existing-lease",
        run_id="run-1",
        client_id="codex-local",
        role="builder",
        actor="builder",
    )))
    store.save_work_item(work_item("work-balance", priority=5))

    result = scheduler.schedule_work_item("work-balance")

    assert result.status_code == 200
    assert result.selected_client_id == "vscode-local"
    assert store.load_work_item("work-balance").assigned_client_id == "vscode-local"


def test_scheduler_denials_are_replayable_and_do_not_mutate_work(tmp_path):
    store = ServerStore(tmp_path / "vad.sqlite3")
    scheduler = WorkItemSchedulerService(store)
    store.save_work_item(work_item("work-no-client", priority=5))

    no_client = scheduler.schedule_work_item("work-no-client")
    store.update_work_item(update_work_item(
        store.load_work_item("work-no-client"),
        status=WorkItemStatus.CANCELLED,
    ))
    not_schedulable = scheduler.schedule_work_item("work-no-client")
    no_work = scheduler.schedule_next(run_id="missing-run")
    events = store.list_control_plane_events()

    assert no_client.status_code == 409
    assert no_client.decision.allow is False
    assert no_client.event.kind == "policy_denied"
    assert "no active trusted clients match" in no_client.decision.denials[0]
    assert not_schedulable.status_code == 409
    assert not_schedulable.event.kind == "policy_denied"
    assert "not schedulable" in not_schedulable.decision.denials[0]
    assert no_work.status_code == 404
    assert no_work.event is None
    assert store.load_work_item("work-no-client").status == WorkItemStatus.CANCELLED
    assert [event.kind.value for event in events] == ["policy_denied", "policy_denied"]
