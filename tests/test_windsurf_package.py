import json
from pathlib import Path

from vad.adapters.mcp import handle_json_rpc_request
from vad.control_plane.events import ControlPlaneEventStatus
from vad.control_plane.plugins import PluginTargetClient, audit_plugin_artifact_security, create_plugin_installer_dry_run
from vad.control_plane.windsurf_package import (
    build_windsurf_package,
    windsurf_mcp_config,
    windsurf_rule_files,
    windsurf_workflow_files,
)
from vad.server.db.store import ServerStore


def test_windsurf_package_manifest_and_current_mcp_config_are_valid():
    package = build_windsurf_package()

    assert package.manifest.plugin_id == "vad-windsurf-local"
    assert package.manifest.target_client == PluginTargetClient.WINDSURF
    assert package.manifest.command.executable == "vad"
    assert package.manifest.command.args == ("mcp", "run")
    assert package.mcp_config == windsurf_mcp_config()
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
        ".codeium/windsurf/mcp_config.json",
        ".devin/rules/vad-builder.md",
        ".devin/rules/vad-verifier.md",
        ".devin/rules/vad-auditor.md",
        ".windsurf/workflows/vad-verify.md",
    ]
    assert all(not tool.approved_by_default for tool in package.manifest.tools)
    assert package.manual_fallback == "vad mcp run"


def test_windsurf_package_rules_and_workflow_are_generated():
    package = build_windsurf_package()
    rules = windsurf_rule_files()
    workflows = windsurf_workflow_files()

    assert package.rule_files == rules
    assert package.workflow_files == workflows
    assert set(rules) == {"builder", "verifier", "auditor"}
    for role, text in rules.items():
        assert text.startswith("---\n")
        assert "trigger: model_decision" in text
        assert "description:" in text
        assert f"# VAD {role.title()}" in text
        assert "VAD" in text
    assert workflows["vad-verify"].startswith("# VAD Verify")
    assert "full Docker-under-WSL gate" in workflows["vad-verify"]
    assert {prompt.path for prompt in package.manifest.prompts} == {
        ".devin/rules/vad-builder.md",
        ".devin/rules/vad-verifier.md",
        ".devin/rules/vad-auditor.md",
    }


def test_windsurf_package_dry_run_and_security_audit_are_clean(tmp_path):
    package = build_windsurf_package()
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
    serialized_workflows = json.dumps(package.workflow_files, sort_keys=True)

    assert audit.passed is True
    assert audit.findings == ()
    assert [operation.path for operation in dry_run.operations] == [
        str((user_config_root / ".codeium" / "windsurf" / "mcp_config.json").resolve()),
        str((workspace_root / ".devin" / "rules" / "vad-builder.md").resolve()),
        str((workspace_root / ".devin" / "rules" / "vad-verifier.md").resolve()),
        str((workspace_root / ".devin" / "rules" / "vad-auditor.md").resolve()),
        str((workspace_root / ".windsurf" / "workflows" / "vad-verify.md").resolve()),
    ]
    for payload in [serialized_config, serialized_rules, serialized_workflows]:
        assert "sk-" not in payload
        assert "token" not in payload.lower()
        assert "password" not in payload.lower()
    assert not workspace_root.exists()
    assert not user_config_root.exists()


def test_windsurf_package_smoke_discovers_tools_and_emits_event(tmp_path):
    package = build_windsurf_package()
    command = package.mcp_config["mcpServers"]["vad"]
    assert command["command"] == "vad"
    assert command["args"] == ["mcp", "run"]

    list_response = handle_json_rpc_request({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/list",
        "params": {"client_id": "windsurf-local", "role": "verifier"},
    })
    tool_names = {tool["name"] for tool in list_response["result"]["tools"]}
    assert {"validate_eip", "repo_assess", "run_proofs", "evidence_inspect"} <= tool_names
    assert "repo_patch" not in tool_names

    evidence_file = tmp_path / "evidence.json"
    db_path = tmp_path / "control-plane.sqlite3"
    evidence_file.write_text(json.dumps({"run_id": "windsurf-run"}), encoding="utf-8")
    call_response = handle_json_rpc_request({
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/call",
        "params": {
            "name": "evidence_inspect",
            "arguments": {
                "file": str(evidence_file),
                "control_plane_db": str(db_path),
                "client_id": "windsurf-local",
                "actor_id": "windsurf-verifier",
                "role": "verifier",
                "run_id": "windsurf-run",
            },
        },
    })
    events = ServerStore(db_path).list_control_plane_events()

    assert call_response["id"] == 2
    assert call_response["result"]["mcp_attribution"]["client_id"] == "windsurf-local"
    assert [event.status for event in events] == [
        ControlPlaneEventStatus.ACTIVE,
        ControlPlaneEventStatus.PASSED,
    ]


def test_windsurf_package_docs_include_current_paths_rules_workflows_and_smoke_notes():
    text = Path("docs/integrations/windsurf.md").read_text(encoding="utf-8")

    assert "vad-windsurf-local" in text
    assert "`~/.codeium/windsurf/mcp_config.json`" in text
    assert '"mcpServers"' in text
    assert '"VAD_LIVE_SERVICES": "disabled"' in text
    assert "`.devin/rules/vad-builder.md`" in text
    assert "`.devin/rules/vad-verifier.md`" in text
    assert "`.devin/rules/vad-auditor.md`" in text
    assert "`.windsurf/workflows/vad-verify.md`" in text
    assert "vad mcp run" in text
