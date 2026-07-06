import json
from pathlib import Path

from vad.adapters.mcp import handle_json_rpc_request
from vad.control_plane.cursor_package import build_cursor_package, cursor_mcp_config, cursor_rule_files
from vad.control_plane.events import ControlPlaneEventStatus
from vad.control_plane.plugins import PluginTargetClient, audit_plugin_artifact_security, create_plugin_installer_dry_run
from vad.server.db.store import ServerStore


def test_cursor_package_manifest_and_current_mcp_config_are_valid():
    package = build_cursor_package()

    assert package.manifest.plugin_id == "vad-cursor-local"
    assert package.manifest.target_client == PluginTargetClient.CURSOR
    assert package.manifest.command.executable == "vad"
    assert package.manifest.command.args == ("mcp", "run")
    assert package.mcp_config == cursor_mcp_config()
    assert package.mcp_config == {
        "mcpServers": {
            "vad": {
                "type": "stdio",
                "command": "vad",
                "args": ["mcp", "run"],
                "env": {"VAD_LIVE_SERVICES": "disabled"},
            }
        }
    }
    assert [path.path for path in package.manifest.config_paths] == [
        ".cursor/mcp.json",
        ".cursor/rules/vad-builder.mdc",
        ".cursor/rules/vad-verifier.mdc",
        ".cursor/rules/vad-auditor.mdc",
        ".cursor/mcp.json",
    ]
    assert all(not tool.approved_by_default for tool in package.manifest.tools)
    assert package.manual_fallback == "vad mcp run"


def test_cursor_package_rules_are_mdc_files_with_frontmatter():
    package = build_cursor_package()
    rules = cursor_rule_files()

    assert package.rule_files == rules
    assert set(rules) == {"builder", "verifier", "auditor"}
    for role, text in rules.items():
        assert text.startswith("---\n")
        assert "description:" in text
        assert "alwaysApply: false" in text
        assert f"# VAD {role.title()}" in text
        assert "VAD" in text
    assert {prompt.path for prompt in package.manifest.prompts} == {
        ".cursor/rules/vad-builder.mdc",
        ".cursor/rules/vad-verifier.mdc",
        ".cursor/rules/vad-auditor.mdc",
    }


def test_cursor_package_dry_run_and_security_audit_are_clean(tmp_path):
    package = build_cursor_package()
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
    serialized_rules = json.dumps(package.rule_files, sort_keys=True)

    assert audit.passed is True
    assert audit.findings == ()
    assert [operation.path for operation in dry_run.operations] == [
        str((workspace_root / ".cursor" / "mcp.json").resolve()),
        str((workspace_root / ".cursor" / "rules" / "vad-builder.mdc").resolve()),
        str((workspace_root / ".cursor" / "rules" / "vad-verifier.mdc").resolve()),
        str((workspace_root / ".cursor" / "rules" / "vad-auditor.mdc").resolve()),
        str((user_config_root / ".cursor" / "mcp.json").resolve()),
    ]
    assert "sk-" not in serialized_config
    assert "token" not in serialized_config.lower()
    assert "password" not in serialized_config.lower()
    assert "sk-" not in serialized_rules
    assert "token" not in serialized_rules.lower()
    assert "password" not in serialized_rules.lower()
    assert not workspace_root.exists()
    assert not user_config_root.exists()


def test_cursor_package_smoke_discovers_tools_and_emits_event(tmp_path):
    package = build_cursor_package()
    command = package.mcp_config["mcpServers"]["vad"]
    assert command["command"] == "vad"
    assert command["args"] == ["mcp", "run"]

    list_response = handle_json_rpc_request({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/list",
        "params": {"client_id": "cursor-local", "role": "verifier"},
    })
    tool_names = {tool["name"] for tool in list_response["result"]["tools"]}
    assert {"validate_eip", "repo_assess", "run_proofs", "evidence_inspect"} <= tool_names
    assert "repo_patch" not in tool_names

    evidence_file = tmp_path / "evidence.json"
    db_path = tmp_path / "control-plane.sqlite3"
    evidence_file.write_text(json.dumps({"run_id": "cursor-run"}), encoding="utf-8")
    call_response = handle_json_rpc_request({
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/call",
        "params": {
            "name": "evidence_inspect",
            "arguments": {
                "file": str(evidence_file),
                "control_plane_db": str(db_path),
                "client_id": "cursor-local",
                "actor_id": "cursor-verifier",
                "role": "verifier",
                "run_id": "cursor-run",
            },
        },
    })
    events = ServerStore(db_path).list_control_plane_events()

    assert call_response["id"] == 2
    assert call_response["result"]["mcp_attribution"]["client_id"] == "cursor-local"
    assert [event.status for event in events] == [
        ControlPlaneEventStatus.ACTIVE,
        ControlPlaneEventStatus.PASSED,
    ]


def test_cursor_package_docs_include_current_paths_rules_and_smoke_notes():
    text = Path("docs/integrations/cursor.md").read_text(encoding="utf-8")

    assert "vad-cursor-local" in text
    assert "`.cursor/mcp.json`" in text
    assert "`~/.cursor/mcp.json`" in text
    assert '"mcpServers"' in text
    assert '"type": "stdio"' in text
    assert '"VAD_LIVE_SERVICES": "disabled"' in text
    assert "`.cursor/rules/vad-builder.mdc`" in text
    assert "`.cursor/rules/vad-verifier.mdc`" in text
    assert "`.cursor/rules/vad-auditor.mdc`" in text
    assert "vad mcp run" in text
