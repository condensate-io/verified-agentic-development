from vad.control_plane.clients import ClientHeartbeat, ClientManifest
from vad.control_plane.events import ControlPlaneEvent
from vad.control_plane.sdk import LocalControlPlaneClient
from vad.control_plane.work_items import WorkItem, WorkItemStatus
from vad.server.api.work_items import WorkItemService


def test_local_control_plane_client_registers_heartbeats_and_emits_events(tmp_path):
    client = LocalControlPlaneClient.from_db_path(tmp_path / "vad.sqlite3")

    registered = client.register(ClientManifest(
        client_id="codex-local",
        display_name="Codex",
        client_type="codex",
        version="1.0.0",
        connection_mode="mcp",
        supported_capabilities=("repo_read",),
        workspace_root=tmp_path,
    ))
    heartbeat = client.heartbeat(ClientHeartbeat(
        client_id="codex-local",
        run_id="run-1",
        task_id="build",
        actor="codex",
        role="builder",
    ))
    emitted = client.emit_event(ControlPlaneEvent(
        event_id="event-1",
        sequence=3,
        client_id="codex-local",
        run_id="run-1",
        task_id="build",
        kind="tool_call_started",
        status="active",
        actor="codex",
        role="builder",
        summary="Codex started a tool call.",
    ))

    assert registered.manifest.client_id == "codex-local"
    assert heartbeat.snapshot.status == "active"
    assert emitted.decision.allow is True
    assert client.store.list_control_plane_events()[-1].event_id == "event-1"


def test_local_control_plane_client_uses_ingestion_policy(tmp_path):
    client = LocalControlPlaneClient.from_db_path(tmp_path / "vad.sqlite3")

    result = client.emit_event({
        "event_id": "deploy-1",
        "sequence": 1,
        "client_id": "codex-local",
        "kind": "deployment_event",
        "status": "active",
        "actor": "codex",
        "role": "builder",
        "summary": "Attempt deployment.",
    })

    assert result.status_code == 403
    assert result.event.kind == "policy_denied"
    assert result.decision.allow is False


def test_local_control_plane_client_work_intake_accepts_and_completes_work(tmp_path):
    client = LocalControlPlaneClient.from_db_path(tmp_path / "vad.sqlite3")
    client.register(ClientManifest(
        client_id="codex-local",
        display_name="Codex",
        client_type="codex",
        version="1.0.0",
        connection_mode="mcp",
        supported_capabilities=("repo_patch",),
        workspace_root=tmp_path,
        trust_state="trusted",
    ))
    heartbeat = client.heartbeat(ClientHeartbeat(
        client_id="codex-local",
        actor="codex",
        role="builder",
    ))
    WorkItemService(client.store).create(WorkItem(
        work_item_id="sdk-work",
        run_id="run-sdk",
        title="SDK connector work",
        role="builder",
        requested_capability="repo_patch",
        priority=5,
    ), actor="operator", role="operator")

    assigned = client.receive_next_work(run_id="run-sdk", actor="scheduler", role="operator", client_id="control-plane")
    polled = client.poll_assigned_work("codex-local", run_id="run-sdk")
    accepted = client.accept_work("sdk-work", client_id="codex-local", actor="codex", role="builder")
    completed = client.complete_work(
        "sdk-work",
        client_id="codex-local",
        actor="codex",
        role="builder",
        evidence_digest="c" * 64,
    )
    events = client.store.list_control_plane_events()

    assert heartbeat.event.kind == "heartbeat"
    assert assigned.selected_client_id == "codex-local"
    assert [item.work_item_id for item in polled] == ["sdk-work"]
    assert accepted.work_item.status == WorkItemStatus.RUNNING
    assert completed.work_item.status == WorkItemStatus.COMPLETED
    assert completed.work_item.evidence_digest == "c" * 64
    assert [event.kind.value for event in events] == [
        "message",
        "heartbeat",
        "work_item",
        "work_item",
        "work_item",
        "work_item",
    ]
    assert events[-1].status.value == "passed"


def test_local_control_plane_client_work_intake_rejects_blocks_and_fails_work(tmp_path):
    client = LocalControlPlaneClient.from_db_path(tmp_path / "vad.sqlite3")
    client.register(ClientManifest(
        client_id="codex-local",
        display_name="Codex",
        client_type="codex",
        version="1.0.0",
        connection_mode="mcp",
        supported_capabilities=("repo_patch",),
        workspace_root=tmp_path,
        trust_state="trusted",
    ))
    service = WorkItemService(client.store)
    for work_item_id in ["reject-work", "block-work", "fail-work"]:
        service.create(WorkItem(
            work_item_id=work_item_id,
            run_id="run-sdk",
            title=work_item_id,
            role="builder",
            status=WorkItemStatus.ASSIGNED,
            assigned_client_id="codex-local",
        ), actor="operator", role="operator")

    rejected = client.reject_work(
        "reject-work",
        client_id="codex-local",
        actor="codex",
        role="builder",
        reason="local connector is busy",
    )
    blocked = client.block_work(
        "block-work",
        client_id="codex-local",
        actor="codex",
        role="builder",
        reason="needs operator input",
    )
    client.accept_work("fail-work", client_id="codex-local", actor="codex", role="builder")
    failed = client.fail_work("fail-work", client_id="codex-local", actor="codex", role="builder")
    events = client.store.list_control_plane_events()

    assert rejected.work_item.status == WorkItemStatus.REQUEUED
    assert rejected.work_item.assigned_client_id is None
    assert "local connector is busy" in rejected.event.summary
    assert blocked.work_item.status == WorkItemStatus.BLOCKED
    assert blocked.work_item.blocked_reason == "needs operator input"
    assert failed.work_item.status == WorkItemStatus.FAILED
    assert [event.kind.value for event in events] == ["message"] + ["work_item"] * 7
    assert events[-1].status.value == "failed"
