import json
from pathlib import Path

from vad.adapters.mcp import handle_json_rpc_request
from vad.control_plane.events import ControlPlaneEventStatus
from vad.control_plane.opencode_package import build_opencode_package, opencode_agent_files, opencode_jsonc_config
from vad.control_plane.plugins import PluginTargetClient, audit_plugin_artifact_security, create_plugin_installer_dry_run
from vad.server.db.store import ServerStore


def test_opencode_package_manifest_and_current_config_are_valid():
    package = build_opencode_package()

    assert package.manifest.plugin_id == "vad-opencode-local"
    assert package.manifest.target_client == PluginTargetClient.OPENCODE
    assert package.manifest.command.executable == "vad"
    assert package.manifest.command.args == ("mcp", "run")
    assert package.opencode_config == opencode_jsonc_config()
    assert package.opencode_config["$schema"].split(":", 1)[0] == "https"
    assert package.opencode_config["$schema"].endswith("opencode.ai/config.json")
    assert package.opencode_config["mcp"]["vad"] == {
        "type": "local",
        "command": ["vad", "mcp", "run"],
        "enabled": True,
        "environment": {"VAD_LIVE_SERVICES": "disabled"},
    }
    assert [path.path for path in package.manifest.config_paths] == [
        "opencode.jsonc",
        ".opencode/agents/vad-builder.md",
        ".opencode/agents/vad-verifier.md",
        ".opencode/agents/vad-auditor.md",
        ".config/opencode/opencode.json",
    ]
    assert all(not tool.approved_by_default for tool in package.manifest.tools)
    assert package.manual_fallback == "vad mcp run"


def test_opencode_package_agent_permissions_gate_tools_by_role():
    package = build_opencode_package()
    config = package.opencode_config

    assert config["permission"]["vad_*"] == "ask"
    assert config["agent"]["vad-builder"]["permission"].get("vad_repo_patch") != "allow"
    assert config["agent"]["vad-builder"]["permission"]["vad_repo_assess"] == "allow"
    assert config["agent"]["vad-builder"]["permission"]["edit"] == "ask"
    assert config["agent"]["vad-verifier"]["permission"]["edit"] == "deny"
    assert config["agent"]["vad-verifier"]["permission"]["vad_run_proofs"] == "ask"
    assert config["agent"]["vad-auditor"]["permission"]["edit"] == "deny"
    assert config["agent"]["vad-auditor"]["permission"]["vad_retro_analyze"] == "ask"


def test_opencode_package_agent_files_include_markdown_permissions():
    package = build_opencode_package()
    agents = opencode_agent_files()

    assert package.agent_files == agents
    assert set(agents) == {"builder", "verifier", "auditor"}
    for role, text in agents.items():
        assert text.startswith("---\n")
        assert "permission:" in text
        assert "vad_*: ask" in text
        assert f"# VAD {role.title()}" in text
        assert "VAD" in text
    assert "edit: deny" in agents["verifier"]
    assert "edit: deny" in agents["auditor"]
    assert "vad_repo_patch" in agents["builder"]
    assert {prompt.path for prompt in package.manifest.prompts} == {
        ".opencode/agents/vad-builder.md",
        ".opencode/agents/vad-verifier.md",
        ".opencode/agents/vad-auditor.md",
    }


def test_opencode_package_dry_run_and_security_audit_are_clean(tmp_path):
    package = build_opencode_package()
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
    serialized_config = json.dumps(package.opencode_config, sort_keys=True)
    serialized_agents = json.dumps(package.agent_files, sort_keys=True)

    assert audit.passed is True
    assert audit.findings == ()
    assert [operation.path for operation in dry_run.operations] == [
        str((workspace_root / "opencode.jsonc").resolve()),
        str((workspace_root / ".opencode" / "agents" / "vad-builder.md").resolve()),
        str((workspace_root / ".opencode" / "agents" / "vad-verifier.md").resolve()),
        str((workspace_root / ".opencode" / "agents" / "vad-auditor.md").resolve()),
        str((user_config_root / ".config" / "opencode" / "opencode.json").resolve()),
    ]
    for payload in [serialized_config, serialized_agents]:
        assert "sk-" not in payload
        assert "token" not in payload.lower()
        assert "password" not in payload.lower()
    assert not workspace_root.exists()
    assert not user_config_root.exists()


def test_opencode_package_smoke_discovers_tools_and_emits_event(tmp_path):
    package = build_opencode_package()
    command = package.opencode_config["mcp"]["vad"]
    assert command["command"] == ["vad", "mcp", "run"]

    list_response = handle_json_rpc_request({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/list",
        "params": {"client_id": "opencode-local", "role": "builder"},
    })
    tool_names = {tool["name"] for tool in list_response["result"]["tools"]}
    assert {"validate_eip", "repo_assess", "evidence_inspect"} <= tool_names
    assert "repo_patch" not in tool_names

    evidence_file = tmp_path / "evidence.json"
    db_path = tmp_path / "control-plane.sqlite3"
    evidence_file.write_text(json.dumps({"run_id": "opencode-run"}), encoding="utf-8")
    call_response = handle_json_rpc_request({
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/call",
        "params": {
            "name": "evidence_inspect",
            "arguments": {
                "file": str(evidence_file),
                "control_plane_db": str(db_path),
                "client_id": "opencode-local",
                "actor_id": "opencode-builder",
                "role": "builder",
                "run_id": "opencode-run",
            },
        },
    })
    events = ServerStore(db_path).list_control_plane_events()

    assert call_response["id"] == 2
    assert call_response["result"]["mcp_attribution"]["client_id"] == "opencode-local"
    assert [event.status for event in events] == [
        ControlPlaneEventStatus.ACTIVE,
        ControlPlaneEventStatus.PASSED,
    ]


def test_opencode_package_docs_include_current_config_agents_and_tool_gates():
    text = Path("docs/integrations/opencode.md").read_text(encoding="utf-8")

    assert "vad-opencode-local" in text
    assert "`opencode.jsonc`" in text
    assert '".mcp"' not in text
    assert '"mcp"' in text
    assert '"type": "local"' in text
    assert '"command": ["vad", "mcp", "run"]' in text
    assert "`.opencode/agents/vad-builder.md`" in text
    assert "`.opencode/agents/vad-verifier.md`" in text
    assert "`.opencode/agents/vad-auditor.md`" in text
    assert "vad_*" in text
    assert "vad mcp run" in text
