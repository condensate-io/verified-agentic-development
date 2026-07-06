import json
from pathlib import Path

from vad.adapters.mcp import handle_json_rpc_request
from vad.control_plane.codex_package import build_codex_package, codex_mcp_config, codex_plugin_json
from vad.control_plane.events import ControlPlaneEventStatus
from vad.control_plane.plugins import PluginTargetClient, audit_plugin_artifact_security, create_plugin_installer_dry_run
from vad.server.db.store import ServerStore


def test_codex_package_matches_current_plugin_manifest_shape():
    package = build_codex_package()
    plugin_json = codex_plugin_json()

    assert package.manifest.plugin_id == "vad-codex-local"
    assert package.manifest.target_client == PluginTargetClient.CODEX
    assert package.manifest.command.executable == "vad"
    assert package.manifest.command.args == ("mcp", "run")
    assert package.plugin_json == plugin_json
    assert plugin_json["name"] == "vad-codex-local"
    assert plugin_json["version"] == "1.0.0"
    assert plugin_json["skills"] == "./skills/"
    assert plugin_json["mcpServers"] == "./.mcp.json"
    assert "interface" in plugin_json
    assert plugin_json["interface"]["displayName"] == "VAD Local"
    assert plugin_json["interface"]["category"] == "Developer Tools"
    assert "hooks" not in plugin_json
    assert "apps" not in plugin_json


def test_codex_package_mcp_config_and_role_skill_guidance_are_valid():
    package = build_codex_package()

    assert package.mcp_config == codex_mcp_config()
    assert package.mcp_config == {
        "mcpServers": {
            "vad": {
                "command": "vad",
                "args": ["mcp", "run"],
                "env": {"VAD_LIVE_SERVICES": "disabled"},
            }
        }
    }
    assert [path.path for path in package.manifest.config_paths] == [
        ".codex-plugin/plugin.json",
        ".mcp.json",
        "codex/vad/.codex-plugin/plugin.json",
        "codex/vad/.mcp.json",
    ]
    assert {prompt.role for prompt in package.manifest.prompts} == {"builder", "verifier", "auditor"}
    assert set(package.skill_guides) == {"builder", "verifier", "auditor"}
    assert all("VAD" in guide for guide in package.skill_guides.values())
    assert all(not tool.approved_by_default for tool in package.manifest.tools)
    assert package.manual_fallback == "vad mcp run"


def test_codex_package_dry_run_and_security_audit_are_clean(tmp_path):
    package = build_codex_package()
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
    serialized_plugin = json.dumps(package.plugin_json, sort_keys=True)
    serialized_config = json.dumps(package.mcp_config, sort_keys=True)

    assert audit.passed is True
    assert audit.findings == ()
    assert [operation.path for operation in dry_run.operations] == [
        str((workspace_root / ".codex-plugin" / "plugin.json").resolve()),
        str((workspace_root / ".mcp.json").resolve()),
        str((user_config_root / "codex" / "vad" / ".codex-plugin" / "plugin.json").resolve()),
        str((user_config_root / "codex" / "vad" / ".mcp.json").resolve()),
    ]
    assert "sk-" not in serialized_plugin
    assert "token" not in serialized_plugin.lower()
    assert "password" not in serialized_plugin.lower()
    assert "sk-" not in serialized_config
    assert "token" not in serialized_config.lower()
    assert "password" not in serialized_config.lower()
    assert not workspace_root.exists()
    assert not user_config_root.exists()


def test_codex_package_smoke_discovers_tools_and_emits_event(tmp_path):
    package = build_codex_package()
    command = package.mcp_config["mcpServers"]["vad"]
    assert command["command"] == "vad"
    assert command["args"] == ["mcp", "run"]

    list_response = handle_json_rpc_request({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/list",
        "params": {"client_id": "codex-local", "role": "builder"},
    })
    tool_names = {tool["name"] for tool in list_response["result"]["tools"]}
    assert {"validate_eip", "repo_assess", "evidence_inspect"} <= tool_names
    assert "repo_patch" not in tool_names
    assert "policy_override" not in tool_names

    evidence_file = tmp_path / "evidence.json"
    db_path = tmp_path / "control-plane.sqlite3"
    evidence_file.write_text(json.dumps({"run_id": "codex-run"}), encoding="utf-8")
    call_response = handle_json_rpc_request({
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/call",
        "params": {
            "name": "evidence_inspect",
            "arguments": {
                "file": str(evidence_file),
                "control_plane_db": str(db_path),
                "client_id": "codex-local",
                "actor_id": "codex-builder",
                "role": "builder",
                "run_id": "codex-run",
            },
        },
    })
    events = ServerStore(db_path).list_control_plane_events()

    assert call_response["id"] == 2
    assert call_response["result"]["mcp_attribution"]["client_id"] == "codex-local"
    assert [event.status for event in events] == [
        ControlPlaneEventStatus.ACTIVE,
        ControlPlaneEventStatus.PASSED,
    ]


def test_codex_package_docs_include_manifest_mcp_skills_and_fallback():
    text = Path("docs/integrations/codex.md").read_text(encoding="utf-8")

    assert "vad-codex-local" in text
    assert "`.codex-plugin/plugin.json`" in text
    assert "`./skills/`" in text
    assert "`./.mcp.json`" in text
    assert '"VAD_LIVE_SERVICES": "disabled"' in text
    assert "Builder" in text
    assert "Verifier" in text
    assert "Auditor" in text
    assert "vad mcp run" in text
