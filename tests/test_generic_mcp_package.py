import json
from pathlib import Path

from vad.adapters.mcp import handle_json_rpc_request
from vad.control_plane.events import ControlPlaneEventStatus
from vad.control_plane.generic_mcp_package import build_generic_mcp_package, generic_mcp_config_snippets
from vad.control_plane.plugins import PluginTargetClient, audit_plugin_artifact_security, create_plugin_installer_dry_run
from vad.server.db.store import ServerStore


def test_generic_mcp_package_manifest_and_stdio_snippets_are_valid():
    package = build_generic_mcp_package()
    snippets = generic_mcp_config_snippets()

    assert package.manifest.plugin_id == "vad-generic-mcp"
    assert package.manifest.target_client == PluginTargetClient.GENERIC_MCP
    assert package.manifest.command.executable == "vad"
    assert package.manifest.command.args == ("mcp", "run")
    assert package.config_snippets == snippets
    assert snippets["mcpServers"]["mcpServers"]["vad"] == {
        "command": "vad",
        "args": ["mcp", "run"],
        "env": {"VAD_LIVE_SERVICES": "disabled"},
    }
    assert snippets["server"]["vad"]["command"] == "vad"
    assert package.manual_fallback == "vad mcp run"
    assert {tool.name for tool in package.manifest.tools} >= {
        "validate_eip",
        "repo_assess",
        "evidence_inspect",
        "swarm_status",
    }
    assert all(not tool.approved_by_default for tool in package.manifest.tools)


def test_generic_mcp_package_passes_plugin_security_audit(tmp_path):
    package = build_generic_mcp_package()
    workspace_root = tmp_path / "workspace"
    user_config_root = tmp_path / "user-config"
    dry_run = create_plugin_installer_dry_run(
        package.manifest,
        workspace_root=workspace_root,
        user_config_root=user_config_root,
    )

    audit = audit_plugin_artifact_security(
        package.manifest,
        dry_run,
        workspace_root=workspace_root,
        user_config_root=user_config_root,
    )

    assert audit.passed is True
    assert audit.findings == ()
    assert [operation.path for operation in dry_run.operations] == [
        str((workspace_root / ".vad" / "mcp" / "generic-mcp.json").resolve()),
        str((user_config_root / "vad" / "mcp" / "generic-mcp.json").resolve()),
    ]


def test_generic_mcp_package_smoke_discovers_tools_and_emits_event(tmp_path):
    package = build_generic_mcp_package()
    command = package.config_snippets["mcpServers"]["mcpServers"]["vad"]
    assert command["command"] == "vad"
    assert command["args"] == ["mcp", "run"]

    list_response = handle_json_rpc_request({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/list",
        "params": {"client_id": "generic-mcp", "role": "observer"},
    })
    tool_names = {tool["name"] for tool in list_response["result"]["tools"]}
    assert {"validate_eip", "repo_assess", "evidence_inspect"} <= tool_names
    assert "repo_patch" not in tool_names

    evidence_file = tmp_path / "evidence.json"
    db_path = tmp_path / "control-plane.sqlite3"
    evidence_file.write_text(json.dumps({"run_id": "generic-mcp-run"}), encoding="utf-8")
    call_response = handle_json_rpc_request({
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/call",
        "params": {
            "name": "evidence_inspect",
            "arguments": {
                "file": str(evidence_file),
                "control_plane_db": str(db_path),
                "client_id": "generic-mcp",
                "actor_id": "observer-1",
                "role": "observer",
                "run_id": "generic-mcp-run",
            },
        },
    })
    events = ServerStore(db_path).list_control_plane_events()

    assert call_response["id"] == 2
    assert call_response["result"]["mcp_attribution"]["client_id"] == "generic-mcp"
    assert [event.status for event in events] == [
        ControlPlaneEventStatus.ACTIVE,
        ControlPlaneEventStatus.PASSED,
    ]


def test_generic_mcp_package_docs_include_manual_fallback_and_snippets():
    text = Path("docs/integrations/generic_mcp.md").read_text(encoding="utf-8")

    assert "vad-generic-mcp" in text
    assert '"mcpServers"' in text
    assert '"VAD_LIVE_SERVICES": "disabled"' in text
    assert "vad mcp run" in text
    assert "High-risk tools are never approved by default." in text
