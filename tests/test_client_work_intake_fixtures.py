from vad.control_plane.antigravity_discovery import discover_antigravity_integration
from vad.control_plane.claude_code_package import build_claude_code_package
from vad.control_plane.clients import ClientHeartbeat, ClientManifest
from vad.control_plane.codex_package import build_codex_package
from vad.control_plane.cursor_package import build_cursor_package
from vad.control_plane.events import ControlPlaneEvent
from vad.control_plane.generic_mcp_package import build_generic_mcp_package
from vad.control_plane.opencode_package import build_opencode_package
from vad.control_plane.sdk import LocalControlPlaneClient
from vad.control_plane.vscode_package import build_vscode_package
from vad.control_plane.windsurf_package import build_windsurf_package
from vad.control_plane.work_items import WorkItem, WorkItemStatus
from vad.server.api.work_items import WorkItemService


CLIENT_FIXTURES = (
    {
        "client_id": "codex-local",
        "display_name": "Codex",
        "client_type": "codex",
        "connection_mode": "mcp",
        "package_id": "vad-codex-local",
        "package": build_codex_package,
    },
    {
        "client_id": "claude-code-local",
        "display_name": "Claude Code",
        "client_type": "claude_code",
        "connection_mode": "mcp",
        "package_id": "vad-claude-code-local",
        "package": build_claude_code_package,
    },
    {
        "client_id": "vscode-local",
        "display_name": "VS Code",
        "client_type": "vscode",
        "connection_mode": "mcp",
        "package_id": "vad-vscode-local",
        "package": build_vscode_package,
    },
    {
        "client_id": "cursor-local",
        "display_name": "Cursor",
        "client_type": "cursor",
        "connection_mode": "mcp",
        "package_id": "vad-cursor-local",
        "package": build_cursor_package,
    },
    {
        "client_id": "windsurf-local",
        "display_name": "Windsurf",
        "client_type": "windsurf",
        "connection_mode": "mcp",
        "package_id": "vad-windsurf-local",
        "package": build_windsurf_package,
    },
    {
        "client_id": "opencode-local",
        "display_name": "OpenCode",
        "client_type": "opencode",
        "connection_mode": "mcp",
        "package_id": "vad-opencode-local",
        "package": build_opencode_package,
    },
    {
        "client_id": "generic-mcp-local",
        "display_name": "Generic MCP/A2A",
        "client_type": "generic_mcp",
        "connection_mode": "mcp",
        "package_id": "vad-generic-mcp",
        "package": build_generic_mcp_package,
    },
    {
        "client_id": "antigravity-local",
        "display_name": "Antigravity",
        "client_type": "antigravity",
        "connection_mode": "mcp",
        "package_id": "vad-generic-mcp",
        "package": lambda: discover_antigravity_integration().fallback_package,
    },
)


def test_per_client_fixtures_complete_assigned_work_through_local_connector_contract(tmp_path):
    client = LocalControlPlaneClient.from_db_path(tmp_path / "vad.sqlite3")
    service = WorkItemService(client.store)

    for fixture in CLIENT_FIXTURES:
        package = fixture["package"]()
        assert package.manifest.plugin_id == fixture["package_id"]
        capability = f"{fixture['client_id']}-work-intake"
        work_item_id = f"{fixture['client_id']}-work"
        client.register(ClientManifest(
            client_id=fixture["client_id"],
            display_name=fixture["display_name"],
            client_type=fixture["client_type"],
            version="1.0.0",
            connection_mode=fixture["connection_mode"],
            supported_capabilities=(capability,),
            workspace_root=tmp_path,
            trust_state="trusted",
        ))
        client.heartbeat(ClientHeartbeat(
            client_id=fixture["client_id"],
            run_id="run-client-fixtures",
            task_id=work_item_id,
            actor=fixture["client_id"],
            role="builder",
        ))
        service.create(WorkItem(
            work_item_id=work_item_id,
            run_id="run-client-fixtures",
            title=f"{fixture['display_name']} assigned local task",
            role="builder",
            requested_capability=capability,
            priority=10,
        ), actor="operator", role="operator")

        assigned = client.receive_next_work(
            run_id="run-client-fixtures",
            actor="scheduler",
            role="operator",
            client_id="control-plane",
        )
        polled = client.poll_assigned_work(fixture["client_id"], run_id="run-client-fixtures")
        accepted = client.accept_work(
            work_item_id,
            client_id=fixture["client_id"],
            actor=fixture["client_id"],
            role="builder",
        )
        proof = client.emit_event(ControlPlaneEvent(
            sequence=len(client.store.list_control_plane_events()) + 1,
            client_id=fixture["client_id"],
            client_label=fixture["display_name"],
            run_id="run-client-fixtures",
            task_id=work_item_id,
            kind="proof_started",
            status="active",
            actor=fixture["client_id"],
            role="builder",
            summary=f"{fixture['display_name']} emitted local fixture proof.",
        ))
        completed = client.complete_work(
            work_item_id,
            client_id=fixture["client_id"],
            actor=fixture["client_id"],
            role="builder",
            evidence_digest="d" * 64,
        )

        assert assigned.selected_client_id == fixture["client_id"]
        assert [item.work_item_id for item in polled] == [work_item_id]
        assert accepted.work_item.status == WorkItemStatus.RUNNING
        assert proof.decision.allow is True
        assert completed.work_item.status == WorkItemStatus.COMPLETED

    completed_items = client.store.list_work_items(run_id="run-client-fixtures", status=WorkItemStatus.COMPLETED)
    events = client.store.list_control_plane_events(run_id="run-client-fixtures")

    assert {item.work_item_id for item in completed_items} == {
        f"{fixture['client_id']}-work" for fixture in CLIENT_FIXTURES
    }
    assert {event.client_label for event in events if event.kind.value == "proof_started"} == {
        fixture["display_name"] for fixture in CLIENT_FIXTURES
    }
    assert [event.kind.value for event in events].count("work_item") == len(CLIENT_FIXTURES) * 4
