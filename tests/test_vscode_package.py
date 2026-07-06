import json
from pathlib import Path

from vad.adapters.mcp import handle_json_rpc_request
from vad.control_plane.events import ControlPlaneEventStatus
from vad.control_plane.plugins import PluginTargetClient, audit_plugin_artifact_security, create_plugin_installer_dry_run
from vad.control_plane.vscode_package import build_vscode_package, vscode_mcp_config, vscode_workspace_tasks
from vad.server.db.store import ServerStore


def test_vscode_package_manifest_and_current_mcp_config_are_valid():
    package = build_vscode_package()

    assert package.manifest.plugin_id == "vad-vscode-local"
    assert package.manifest.target_client == PluginTargetClient.VS_CODE
    assert package.manifest.command.executable == "vad"
    assert package.manifest.command.args == ("mcp", "run")
    assert package.mcp_config == vscode_mcp_config()
    assert package.mcp_config == {
        "servers": {
            "vad": {
                "type": "stdio",
                "command": "vad",
                "args": ["mcp", "run"],
                "env": {"VAD_LIVE_SERVICES": "disabled"},
            }
        }
    }
    assert [path.path for path in package.manifest.config_paths] == [
        ".vscode/mcp.json",
        ".vscode/tasks.json",
        "vscode/mcp.json",
    ]
    assert all(not tool.approved_by_default for tool in package.manifest.tools)
    assert package.manual_fallback == "vad mcp run"


def test_vscode_package_dashboard_task_is_local_and_reviewable():
    package = build_vscode_package()
    tasks = vscode_workspace_tasks()
    task = tasks["tasks"][0]

    assert package.workspace_tasks == tasks
    assert task["label"] == "VAD: Serve dashboard"
    assert task["command"] == "vad"
    assert task["args"] == [
        "ui",
        "serve",
        "--host",
        "127.0.0.1",
        "--port",
        "8080",
        "--seed-level3-demo",
    ]
    assert package.dashboard_url == "http://127.0.0.1:8080"


def test_vscode_package_dry_run_and_security_audit_are_clean(tmp_path):
    package = build_vscode_package()
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
    serialized_config = json.dumps(package.mcp_config, sort_keys=True)
    serialized_tasks = json.dumps(package.workspace_tasks, sort_keys=True)

    assert audit.passed is True
    assert audit.findings == ()
    assert [operation.path for operation in dry_run.operations] == [
        str((workspace_root / ".vscode" / "mcp.json").resolve()),
        str((workspace_root / ".vscode" / "tasks.json").resolve()),
        str((user_config_root / "vscode" / "mcp.json").resolve()),
    ]
    assert "sk-" not in serialized_config
    assert "token" not in serialized_config.lower()
    assert "password" not in serialized_config.lower()
    assert "sk-" not in serialized_tasks
    assert "token" not in serialized_tasks.lower()
    assert "password" not in serialized_tasks.lower()
    assert not workspace_root.exists()
    assert not user_config_root.exists()


def test_vscode_package_smoke_discovers_tools_and_emits_event(tmp_path):
    package = build_vscode_package()
    command = package.mcp_config["servers"]["vad"]
    assert command["command"] == "vad"
    assert command["args"] == ["mcp", "run"]

    list_response = handle_json_rpc_request({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/list",
        "params": {"client_id": "vscode-local", "role": "verifier"},
    })
    tool_names = {tool["name"] for tool in list_response["result"]["tools"]}
    assert {"validate_eip", "repo_assess", "run_proofs", "evidence_inspect"} <= tool_names
    assert "repo_patch" not in tool_names

    evidence_file = tmp_path / "evidence.json"
    db_path = tmp_path / "control-plane.sqlite3"
    evidence_file.write_text(json.dumps({"run_id": "vscode-run"}), encoding="utf-8")
    call_response = handle_json_rpc_request({
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/call",
        "params": {
            "name": "evidence_inspect",
            "arguments": {
                "file": str(evidence_file),
                "control_plane_db": str(db_path),
                "client_id": "vscode-local",
                "actor_id": "vscode-verifier",
                "role": "verifier",
                "run_id": "vscode-run",
            },
        },
    })
    events = ServerStore(db_path).list_control_plane_events()

    assert call_response["id"] == 2
    assert call_response["result"]["mcp_attribution"]["client_id"] == "vscode-local"
    assert [event.status for event in events] == [
        ControlPlaneEventStatus.ACTIVE,
        ControlPlaneEventStatus.PASSED,
    ]


def test_vscode_package_docs_include_current_paths_dashboard_and_trust_notes():
    text = Path("docs/integrations/vscode.md").read_text(encoding="utf-8")

    assert "vad-vscode-local" in text
    assert "`.vscode/mcp.json`" in text
    assert '"servers"' in text
    assert '"type": "stdio"' in text
    assert '"VAD_LIVE_SERVICES": "disabled"' in text
    assert "MCP: Open User Configuration" in text
    assert "VAD: Serve dashboard" in text
    assert "http://127.0.0.1:8080" in text
    assert "trust" in text.lower()
