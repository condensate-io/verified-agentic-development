import json
from pathlib import Path

from vad.adapters.mcp import handle_json_rpc_request
from vad.control_plane.antigravity_discovery import discover_antigravity_integration
from vad.control_plane.events import ControlPlaneEventStatus
from vad.control_plane.plugins import PluginTargetClient
from vad.server.db.store import ServerStore


def test_antigravity_discovery_records_no_first_class_surface_and_generic_fallback():
    discovery = discover_antigravity_integration()

    assert discovery.target_client == PluginTargetClient.ANTIGRAVITY
    assert discovery.first_class_surface_found is False
    assert discovery.verified_on == "2026-07-03"
    assert "No current public Antigravity documentation" in discovery.evidence_summary
    assert discovery.fallback_package.manifest.plugin_id == "vad-generic-mcp"
    assert discovery.fallback_package.manual_fallback == "vad mcp run"
    assert all(not tool.approved_by_default for tool in discovery.fallback_package.manifest.tools)


def test_antigravity_discovery_fallback_uses_local_stdio_config():
    discovery = discover_antigravity_integration()
    snippets = discovery.fallback_package.config_snippets

    assert snippets["mcpServers"]["mcpServers"]["vad"] == {
        "command": "vad",
        "args": ["mcp", "run"],
        "env": {"VAD_LIVE_SERVICES": "disabled"},
    }
    assert snippets["server"]["vad"]["command"] == "vad"


def test_antigravity_generic_mcp_smoke_discovers_tools_and_emits_event(tmp_path):
    discovery = discover_antigravity_integration()
    command = discovery.fallback_package.config_snippets["mcpServers"]["mcpServers"]["vad"]
    assert command["command"] == "vad"
    assert command["args"] == ["mcp", "run"]

    list_response = handle_json_rpc_request({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/list",
        "params": {"client_id": "antigravity-generic-mcp", "role": "observer"},
    })
    tool_names = {tool["name"] for tool in list_response["result"]["tools"]}
    assert {"validate_eip", "repo_assess", "evidence_inspect"} <= tool_names
    assert "repo_patch" not in tool_names

    evidence_file = tmp_path / "evidence.json"
    db_path = tmp_path / "control-plane.sqlite3"
    evidence_file.write_text(json.dumps({"run_id": "antigravity-run"}), encoding="utf-8")
    call_response = handle_json_rpc_request({
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/call",
        "params": {
            "name": "evidence_inspect",
            "arguments": {
                "file": str(evidence_file),
                "control_plane_db": str(db_path),
                "client_id": "antigravity-generic-mcp",
                "actor_id": "antigravity-observer",
                "role": "observer",
                "run_id": "antigravity-run",
            },
        },
    })
    events = ServerStore(db_path).list_control_plane_events()

    assert call_response["id"] == 2
    assert call_response["result"]["mcp_attribution"]["client_id"] == "antigravity-generic-mcp"
    assert [event.status for event in events] == [
        ControlPlaneEventStatus.ACTIVE,
        ControlPlaneEventStatus.PASSED,
    ]


def test_antigravity_docs_document_fallback_without_first_class_claim():
    text = Path("docs/integrations/antigravity.md").read_text(encoding="utf-8")

    assert "Antigravity Integration Discovery" in text
    assert "No first-class VAD Antigravity package is generated" in text
    assert "vad-generic-mcp" in text
    assert '"VAD_LIVE_SERVICES": "disabled"' in text
    assert "vad mcp run" in text
    assert "Do not claim first-class Antigravity support" in text
