import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from vad.control_plane.plugins import (
    PluginCommand,
    PluginConfigPath,
    PluginConfigScope,
    PluginInventoryRecord,
    PluginInventoryReviewState,
    PluginStatus,
    PluginPermission,
    PluginPrompt,
    PluginTargetClient,
    PluginToolGrant,
    VADPluginManifest,
    audit_plugin_artifact_security,
    apply_plugin_installer_plan,
    compute_plugin_artifact_hash,
    create_plugin_installer_dry_run,
    rollback_plugin_installation,
    seed_plugin_statuses,
    sign_plugin_artifact_hash,
    uninstall_plugin_installation,
    verify_plugin_artifact,
)
from vad.signing.local import LocalDevelopmentSigner
from vad.signing.local import payload_digest


DIGEST = "a" * 64


def valid_manifest(**overrides):
    data = {
        "plugin_id": "vad-codex-local",
        "target_client": PluginTargetClient.CODEX,
        "version": "1.0.0",
        "command": PluginCommand(executable="python", args=("-m", "vad.adapters.mcp")),
        "config_paths": (
            PluginConfigPath(scope=PluginConfigScope.WORKSPACE, path=".codex/vad-mcp.json"),
        ),
        "permissions": (
            PluginPermission(name="mcp_stdio", reason="Expose the local VAD MCP server."),
        ),
        "tools": (
            PluginToolGrant(name="repo_assess", role="observer"),
            PluginToolGrant(name="repo_patch", role="builder", high_risk=True, approved_by_default=False),
        ),
        "prompts": (
            PluginPrompt(prompt_id="vad-builder", role="builder", path="prompts/builder.md"),
        ),
        "checksums": {
            "plugin.json": DIGEST,
            "prompts/builder.md": "b" * 64,
        },
    }
    data.update(overrides)
    return VADPluginManifest(**data)


def test_plugin_manifest_captures_package_contract_fields():
    manifest = valid_manifest()

    assert manifest.plugin_id == "vad-codex-local"
    assert manifest.target_client == PluginTargetClient.CODEX
    assert manifest.version == "1.0.0"
    assert manifest.command.args == ("-m", "vad.adapters.mcp")
    assert manifest.config_paths[0].scope == PluginConfigScope.WORKSPACE
    assert manifest.permissions[0].name == "mcp_stdio"
    assert {tool.name for tool in manifest.tools} == {"repo_assess", "repo_patch"}
    assert manifest.prompts[0].path == "prompts/builder.md"
    assert manifest.checksums == {
        "plugin.json": DIGEST,
        "prompts/builder.md": "b" * 64,
    }


def test_plugin_manifest_rejects_unknown_target_and_bad_version():
    with pytest.raises(ValidationError):
        valid_manifest(target_client="unknown")

    with pytest.raises(ValidationError):
        valid_manifest(version="latest")


def test_plugin_manifest_rejects_unsafe_ids_paths_and_checksums():
    with pytest.raises(ValidationError, match="plugin_id"):
        valid_manifest(plugin_id="../escape")

    with pytest.raises(ValidationError, match="config paths"):
        valid_manifest(config_paths=(PluginConfigPath(scope="workspace", path="../escape.json"),))

    with pytest.raises(ValidationError, match="checksum paths"):
        valid_manifest(checksums={"../plugin.json": DIGEST})

    with pytest.raises(ValidationError):
        valid_manifest(checksums={"plugin.json": "not-a-digest"})


def test_plugin_manifest_rejects_secret_bearing_command_env():
    with pytest.raises(ValidationError, match="secret environment"):
        valid_manifest(command=PluginCommand(
            executable="python",
            args=("-m", "vad.adapters.mcp"),
            env={"OPENAI_API_KEY": "sk-test"},
        ))


def test_plugin_manifest_rejects_high_risk_auto_approval_and_duplicates():
    with pytest.raises(ValidationError, match="high-risk plugin tools"):
        valid_manifest(tools=(
            PluginToolGrant(name="repo_patch", role="builder", high_risk=True, approved_by_default=True),
        ))

    with pytest.raises(ValidationError, match="entries must be unique"):
        valid_manifest(tools=(
            PluginToolGrant(name="repo_assess", role="observer"),
            PluginToolGrant(name="repo_assess", role="observer"),
        ))


def test_plugin_manifest_schema_artifact_matches_model():
    schema_path = Path(__file__).resolve().parents[1] / "schemas" / "vad-plugin-manifest.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))

    assert schema == VADPluginManifest.json_schema()
    properties = schema["properties"]
    for field in [
        "plugin_id",
        "target_client",
        "version",
        "command",
        "config_paths",
        "permissions",
        "tools",
        "prompts",
        "checksums",
    ]:
        assert field in properties


def test_plugin_artifact_hash_is_deterministic_and_manifest_bound():
    manifest = valid_manifest(checksums={
        "prompts/builder.md": "b" * 64,
        "plugin.json": DIGEST,
    })

    artifact_hash = compute_plugin_artifact_hash(manifest)
    repeated = compute_plugin_artifact_hash(manifest)
    changed = compute_plugin_artifact_hash(valid_manifest(checksums={
        "plugin.json": DIGEST,
        "prompts/builder.md": "c" * 64,
    }))

    assert artifact_hash == repeated
    assert artifact_hash.plugin_id == manifest.plugin_id
    assert artifact_hash.version == manifest.version
    assert len(artifact_hash.manifest_digest) == 64
    assert len(artifact_hash.artifact_digest) == 64
    assert artifact_hash.file_digests == {
        "plugin.json": DIGEST,
        "prompts/builder.md": "b" * 64,
    }
    assert changed.artifact_digest != artifact_hash.artifact_digest


def test_plugin_artifact_can_be_signed_with_local_development_signature():
    manifest = valid_manifest()
    artifact_hash = compute_plugin_artifact_hash(manifest)
    signer = LocalDevelopmentSigner("local-plugin-dev", b"plugin-secret")

    signature = sign_plugin_artifact_hash(artifact_hash, signer)
    verification = verify_plugin_artifact(manifest, signature=signature, signer=signer)

    assert signature.signature.payload_digest == payload_digest(artifact_hash.model_dump(mode="json"))
    assert verification.signature_present is True
    assert verification.signature_verified is True
    assert verification.signer_key_id == "local-plugin-dev"
    assert verification.artifact_digest == artifact_hash.artifact_digest
    assert verification.evidence == "Plugin artifact signature verified."


def test_plugin_artifact_verification_records_unsigned_evidence():
    verification = verify_plugin_artifact(valid_manifest())

    assert verification.signature_present is False
    assert verification.signature_verified is False
    assert verification.signer_key_id is None
    assert verification.file_count == 2
    assert verification.evidence == "Plugin artifact digest verified."


def test_plugin_artifact_verification_fails_for_tampered_manifest():
    original = valid_manifest()
    tampered = valid_manifest(version="1.0.1")
    signer = LocalDevelopmentSigner("local-plugin-dev", b"plugin-secret")
    signature = sign_plugin_artifact_hash(compute_plugin_artifact_hash(original), signer)

    verification = verify_plugin_artifact(tampered, signature=signature, signer=signer)

    assert verification.signature_present is True
    assert verification.signature_verified is False
    assert verification.signer_key_id == "local-plugin-dev"
    assert verification.evidence == "Plugin artifact signature verification failed."


def test_plugin_installer_dry_run_lists_exact_writes_and_rollback(tmp_path):
    manifest = valid_manifest(config_paths=(
        PluginConfigPath(scope=PluginConfigScope.WORKSPACE, path=".codex/vad-mcp.json"),
        PluginConfigPath(scope=PluginConfigScope.USER, path="codex/vad-mcp.json"),
    ))
    workspace_root = tmp_path / "workspace"
    user_config_root = tmp_path / "user-config"

    dry_run = create_plugin_installer_dry_run(
        manifest,
        workspace_root=workspace_root,
        user_config_root=user_config_root,
    )

    assert dry_run.dry_run is True
    assert dry_run.writes_performed == 0
    assert [operation.path for operation in dry_run.operations] == [
        str((workspace_root / ".codex" / "vad-mcp.json").resolve()),
        str((user_config_root / "codex" / "vad-mcp.json").resolve()),
    ]
    assert dry_run.operations[0].change == "write vad-codex-local config for codex"
    assert dry_run.operations[0].content["plugin_id"] == "vad-codex-local"
    assert dry_run.operations[0].content["artifact_digest"] == dry_run.artifact.artifact_digest
    assert [rollback.action for rollback in dry_run.rollback] == ["restore_or_remove", "restore_or_remove"]
    assert dry_run.rollback[0].backup_path.endswith(".vad-backup")
    assert not (workspace_root / ".codex").exists()
    assert not user_config_root.exists()


def test_plugin_installer_dry_run_rejects_project_scope_and_path_escape(tmp_path):
    with pytest.raises(ValueError, match="limited to user and workspace"):
        create_plugin_installer_dry_run(
            valid_manifest(config_paths=(PluginConfigPath(scope=PluginConfigScope.PROJECT, path=".vad/plugin.json"),)),
            workspace_root=tmp_path / "workspace",
            user_config_root=tmp_path / "user-config",
        )

    with pytest.raises(ValidationError, match="config paths"):
        valid_manifest(config_paths=(PluginConfigPath(scope=PluginConfigScope.WORKSPACE, path="../outside.json"),))


def test_seed_plugin_statuses_cover_dashboard_states():
    statuses = seed_plugin_statuses()

    assert {status.status.value for status in statuses} == {"installed", "available", "failed", "needs_review"}
    assert {status.publication_readiness for status in statuses} == {
        "local_ready",
        "dry_run_ready",
        "needs_operator_review",
        "blocked",
    }
    assert {status.target_client for status in statuses} >= {
        PluginTargetClient.CODEX,
        PluginTargetClient.CLAUDE_CODE,
        PluginTargetClient.VS_CODE,
        PluginTargetClient.CURSOR,
    }
    assert all(status.local_version == status.version for status in statuses)
    assert all(status.summary for status in statuses)


def test_plugin_inventory_record_tracks_review_apply_uninstall_and_rollback_state():
    record = PluginInventoryRecord(
        plugin_id="vad-codex-local",
        target_client=PluginTargetClient.CODEX,
        version="1.0.0",
        review_state=PluginInventoryReviewState.APPROVED,
        applied_config_hashes={".codex-plugin/plugin.json": DIGEST},
        backup_paths=(".codex-plugin/plugin.json.vad-backup",),
        uninstall_status="available",
        rollback_status="ready",
        dashboard_status=PluginStatus.INSTALLED,
        publication_readiness="local_ready",
        summary="Codex plugin inventory approved.",
        artifact_digest=DIGEST,
        manifest_digest=DIGEST,
    )
    status = record.to_status_record()

    assert record.applied_config_hashes[".codex-plugin/plugin.json"] == DIGEST
    assert record.backup_paths == (".codex-plugin/plugin.json.vad-backup",)
    assert status.status == PluginStatus.INSTALLED
    assert status.local_version == "1.0.0"
    with pytest.raises(ValidationError, match="applied config hash paths"):
        PluginInventoryRecord(
            plugin_id="vad-bad",
            target_client=PluginTargetClient.CODEX,
            version="1.0.0",
            applied_config_hashes={"../escape": DIGEST},
            summary="Bad inventory.",
        )


def test_plugin_apply_writes_configs_records_backups_and_inventory(tmp_path):
    manifest = valid_manifest(config_paths=(
        PluginConfigPath(scope=PluginConfigScope.WORKSPACE, path=".codex/vad-mcp.json"),
        PluginConfigPath(scope=PluginConfigScope.USER, path="codex/vad-mcp.json"),
    ))
    workspace_root = tmp_path / "workspace"
    user_config_root = tmp_path / "user-config"
    existing_workspace_config = workspace_root / ".codex" / "vad-mcp.json"
    existing_workspace_config.parent.mkdir(parents=True)
    existing_workspace_config.write_text('{"old": true}\n', encoding="utf-8")
    dry_run = create_plugin_installer_dry_run(
        manifest,
        workspace_root=workspace_root,
        user_config_root=user_config_root,
    )

    result = apply_plugin_installer_plan(
        dry_run,
        workspace_root=workspace_root,
        user_config_root=user_config_root,
        approval_ref="approval:plugin-apply-1",
    )

    assert result.status == "applied"
    assert result.writes_performed == 2
    assert result.inventory.review_state == PluginInventoryReviewState.APPROVED
    assert result.inventory.dashboard_status == PluginStatus.INSTALLED
    assert set(result.applied_config_hashes) == {
        "workspace/.codex/vad-mcp.json",
        "user/codex/vad-mcp.json",
    }
    assert result.backup_paths == ("workspace/.codex/vad-mcp.json.vad-backup",)
    assert json.loads(existing_workspace_config.read_text(encoding="utf-8"))["plugin_id"] == "vad-codex-local"
    assert (workspace_root / ".codex" / "vad-mcp.json.vad-backup").read_text(encoding="utf-8") == '{"old": true}\n'


def test_plugin_rollback_restores_backups_and_removes_created_configs(tmp_path):
    manifest = valid_manifest(config_paths=(
        PluginConfigPath(scope=PluginConfigScope.WORKSPACE, path=".codex/vad-mcp.json"),
        PluginConfigPath(scope=PluginConfigScope.USER, path="codex/vad-mcp.json"),
    ))
    workspace_root = tmp_path / "workspace"
    user_config_root = tmp_path / "user-config"
    existing_workspace_config = workspace_root / ".codex" / "vad-mcp.json"
    existing_workspace_config.parent.mkdir(parents=True)
    existing_workspace_config.write_text('{"old": true}\n', encoding="utf-8")
    dry_run = create_plugin_installer_dry_run(
        manifest,
        workspace_root=workspace_root,
        user_config_root=user_config_root,
    )
    applied = apply_plugin_installer_plan(
        dry_run,
        workspace_root=workspace_root,
        user_config_root=user_config_root,
        approval_ref="approval:plugin-apply-1",
    )

    rollback = rollback_plugin_installation(
        dry_run,
        applied.inventory,
        workspace_root=workspace_root,
        user_config_root=user_config_root,
        approval_ref="approval:plugin-rollback-1",
    )

    assert rollback.status == "rolled_back"
    assert rollback.inventory.rollback_status == "rolled_back"
    assert rollback.inventory.dashboard_status == PluginStatus.AVAILABLE
    assert existing_workspace_config.read_text(encoding="utf-8") == '{"old": true}\n'
    assert not (user_config_root / "codex" / "vad-mcp.json").exists()


def test_plugin_uninstall_removes_configs_and_blocks_drift(tmp_path):
    manifest = valid_manifest()
    workspace_root = tmp_path / "workspace"
    user_config_root = tmp_path / "user-config"
    dry_run = create_plugin_installer_dry_run(
        manifest,
        workspace_root=workspace_root,
        user_config_root=user_config_root,
    )
    applied = apply_plugin_installer_plan(
        dry_run,
        workspace_root=workspace_root,
        user_config_root=user_config_root,
        approval_ref="approval:plugin-apply-1",
    )
    target = workspace_root / ".codex" / "vad-mcp.json"
    target.write_text('{"drifted": true}\n', encoding="utf-8")

    with pytest.raises(ValueError, match="drifted"):
        uninstall_plugin_installation(
            dry_run,
            applied.inventory,
            workspace_root=workspace_root,
            user_config_root=user_config_root,
            approval_ref="approval:plugin-uninstall-1",
        )

    target.write_bytes(json.dumps(dry_run.operations[0].content, sort_keys=True, indent=2).encode("utf-8") + b"\n")
    uninstalled = uninstall_plugin_installation(
        dry_run,
        applied.inventory,
        workspace_root=workspace_root,
        user_config_root=user_config_root,
        approval_ref="approval:plugin-uninstall-1",
    )

    assert uninstalled.status == "uninstalled"
    assert uninstalled.inventory.uninstall_status == "uninstalled"
    assert uninstalled.inventory.applied_config_hashes == {}
    assert not target.exists()


def test_plugin_apply_rejects_tampered_write_and_backup_paths(tmp_path):
    manifest = valid_manifest()
    workspace_root = tmp_path / "workspace"
    user_config_root = tmp_path / "user-config"
    dry_run = create_plugin_installer_dry_run(
        manifest,
        workspace_root=workspace_root,
        user_config_root=user_config_root,
    )
    outside_operation = dry_run.operations[0].model_copy(update={"path": str((tmp_path / "outside.json").resolve())})
    outside_backup = dry_run.rollback[0].model_copy(update={"backup_path": str((tmp_path / "outside.bak").resolve())})

    with pytest.raises(ValueError, match="escapes approved"):
        apply_plugin_installer_plan(
            dry_run.model_copy(update={"operations": (outside_operation,)}),
            workspace_root=workspace_root,
            user_config_root=user_config_root,
            approval_ref="approval:plugin-apply-1",
        )

    with pytest.raises(ValueError, match="escapes approved"):
        apply_plugin_installer_plan(
            dry_run.model_copy(update={"rollback": (outside_backup,)}),
            workspace_root=workspace_root,
            user_config_root=user_config_root,
            approval_ref="approval:plugin-apply-1",
        )


def test_plugin_artifact_security_audit_passes_clean_dry_run(tmp_path):
    manifest = valid_manifest()
    workspace_root = tmp_path / "workspace"
    user_config_root = tmp_path / "user-config"
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

    assert audit.passed is True
    assert audit.findings == ()


def test_plugin_artifact_security_audit_detects_secret_generated_config(tmp_path):
    manifest = valid_manifest()
    workspace_root = tmp_path / "workspace"
    user_config_root = tmp_path / "user-config"
    dry_run = create_plugin_installer_dry_run(
        manifest,
        workspace_root=workspace_root,
        user_config_root=user_config_root,
    )
    operation = dry_run.operations[0].model_copy(update={
        "content": {**dry_run.operations[0].content, "env": {"API_TOKEN": "sk-local-test-token"}},
    })
    tainted = dry_run.model_copy(update={"operations": (operation,)})

    audit = audit_plugin_artifact_security(
        manifest,
        tainted,
        workspace_root=workspace_root,
        user_config_root=user_config_root,
    )

    assert audit.passed is False
    assert "no_secrets" in {finding.check for finding in audit.findings}


def test_plugin_artifact_security_audit_detects_unguarded_write_path(tmp_path):
    manifest = valid_manifest()
    workspace_root = tmp_path / "workspace"
    user_config_root = tmp_path / "user-config"
    dry_run = create_plugin_installer_dry_run(
        manifest,
        workspace_root=workspace_root,
        user_config_root=user_config_root,
    )
    operation = dry_run.operations[0].model_copy(update={"path": str((tmp_path / "outside.json").resolve())})
    tainted = dry_run.model_copy(update={"operations": (operation,)})

    audit = audit_plugin_artifact_security(
        manifest,
        tainted,
        workspace_root=workspace_root,
        user_config_root=user_config_root,
    )

    assert audit.passed is False
    assert "guarded_write" in {finding.check for finding in audit.findings}


def test_plugin_artifact_security_audit_rejects_auto_approved_dangerous_tools():
    with pytest.raises(ValidationError, match="high-risk plugin tools"):
        valid_manifest(tools=(
            PluginToolGrant(name="repo_patch", role="builder", high_risk=True, approved_by_default=True),
        ))


def test_plugin_artifact_security_audit_detects_cloud_endpoint_default(tmp_path):
    manifest = valid_manifest(command=PluginCommand(
        executable="python",
        args=("-m", "vad.adapters.mcp", "--endpoint", "https://" + "example.com/vad"),
    ))
    workspace_root = tmp_path / "workspace"
    user_config_root = tmp_path / "user-config"
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

    assert audit.passed is False
    assert "no_cloud_default" in {finding.check for finding in audit.findings}
