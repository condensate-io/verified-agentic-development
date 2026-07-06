import json
from pathlib import Path

from vad.adapters.mcp import handle_json_rpc_request
from vad.control_plane.artifacts import build_reproducible_artifacts
from vad.control_plane.plugins import (
    VADPluginManifest,
    audit_plugin_artifact_security,
    create_plugin_installer_dry_run,
)


def test_generated_artifacts_support_temp_home_install_dry_run_and_rollback(tmp_path):
    artifact_root = tmp_path / "artifacts"
    workspace_root = tmp_path / "workspace"
    user_config_root = tmp_path / "home" / ".config"
    report = build_reproducible_artifacts(artifact_root)
    manifest_artifacts = [
        artifact for artifact in report.artifacts
        if artifact.path.startswith("plugins/") and artifact.path.endswith("/manifest.json")
    ]

    assert manifest_artifacts
    for artifact in manifest_artifacts:
        manifest_path = artifact_root / artifact.path
        manifest = VADPluginManifest(**json.loads(manifest_path.read_text(encoding="utf-8")))
        dry_run = create_plugin_installer_dry_run(
            manifest,
            workspace_root=workspace_root,
            user_config_root=user_config_root,
        )
        audit = audit_plugin_artifact_security(
            manifest,
            dry_run,
            workspace_root=workspace_root,
            user_config_root=user_config_root,
        )

        assert dry_run.dry_run is True
        assert dry_run.writes_performed == 0
        assert dry_run.operations
        assert len(dry_run.rollback) == len(dry_run.operations)
        assert audit.passed is True
        assert all(rollback.action == "restore_or_remove" for rollback in dry_run.rollback)
        assert all(rollback.backup_path.endswith(".vad-backup") for rollback in dry_run.rollback)

    assert not workspace_root.exists()
    assert not user_config_root.exists()


def test_installed_artifact_smoke_discovers_safe_mcp_tools_from_artifact_command(tmp_path):
    artifact_root = tmp_path / "artifacts"
    build_reproducible_artifacts(artifact_root)
    manifest_path = artifact_root / "plugins" / "vad-generic-mcp" / "1.0.0" / "manifest.json"
    manifest = VADPluginManifest(**json.loads(manifest_path.read_text(encoding="utf-8")))

    assert manifest.command.executable == "vad"
    assert manifest.command.args == ("mcp", "run")

    response = handle_json_rpc_request({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/list",
        "params": {"client_id": "installed-artifact-smoke", "role": "observer"},
    })
    tool_names = {tool["name"] for tool in response["result"]["tools"]}

    assert {"validate_eip", "repo_assess", "evidence_inspect"} <= tool_names
    assert "repo_patch" not in tool_names


def test_installed_artifact_smoke_records_manual_uninstall_boundary(tmp_path):
    artifact_root = tmp_path / "artifacts"
    workspace_root = tmp_path / "workspace"
    user_config_root = tmp_path / "home" / ".config"
    build_reproducible_artifacts(artifact_root)
    manifest_path = artifact_root / "plugins" / "vad-codex-local" / "1.0.0" / "manifest.json"
    manifest = VADPluginManifest(**json.loads(manifest_path.read_text(encoding="utf-8")))
    dry_run = create_plugin_installer_dry_run(
        manifest,
        workspace_root=workspace_root,
        user_config_root=user_config_root,
    )

    planned_paths = {Path(operation.path).name for operation in dry_run.operations}
    rollback_paths = {Path(rollback.path).name for rollback in dry_run.rollback}
    assert planned_paths == rollback_paths
    assert dry_run.rollback
    assert all(rollback.action == "restore_or_remove" for rollback in dry_run.rollback)
