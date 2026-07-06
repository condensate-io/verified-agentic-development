import json
from pathlib import Path

from vad.control_plane.artifacts import build_reproducible_artifacts
from vad.control_plane.plugins import (
    VADPluginManifest,
    audit_plugin_artifact_security,
    create_plugin_installer_dry_run,
)


ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "plugin-publication-security-audit.md"


def test_plugin_publication_security_audit_covers_required_checks():
    text = DOC.read_text(encoding="utf-8")

    for expected in [
        "## Artifact Scans",
        "`build_reproducible_artifacts`",
        "`digest-report.json`",
        "`secret_scan_passed` is `true`",
        "`schemas/vad-plugin-manifest.schema.json`",
        "## Manifest Validation",
        "`VADPluginManifest`",
        "schemas/vad-plugin-manifest.schema.json",
        "64-character lowercase hexadecimal digests",
        "## No Auto-Trust Or Auto-Approval",
        "`dry_run=True`",
        "`writes_performed=0`",
        "`restore_or_remove`",
        "`repo_patch`",
        "`repo_run`",
        "`sign_verify`",
        "`approved_by_default=True`",
        "`dangerous_auto_approval`",
        "explicit human approval",
        "## Local-Only Defaults",
        "`vad mcp run`",
        "`no_cloud_default`",
        "not approve publication",
        "marketplace upload",
        "registry upload",
        "automatic trust",
    ]:
        assert expected in text


def test_generated_plugin_publication_artifacts_pass_security_audit(tmp_path):
    artifact_root = tmp_path / "artifacts"
    report = build_reproducible_artifacts(artifact_root)
    digest_report = json.loads((artifact_root / "digest-report.json").read_text(encoding="utf-8"))
    manifest_artifacts = [
        artifact for artifact in report.artifacts
        if artifact.path.startswith("plugins/") and artifact.path.endswith("/manifest.json")
    ]

    assert report.secret_scan_passed is True
    assert digest_report["secret_scan_passed"] is True
    assert manifest_artifacts
    assert any(artifact.path == "schemas/vad-plugin-manifest.schema.json" for artifact in report.artifacts)

    for artifact in manifest_artifacts:
        manifest_path = artifact_root / artifact.path
        manifest = VADPluginManifest(**json.loads(manifest_path.read_text(encoding="utf-8")))
        dry_run = create_plugin_installer_dry_run(
            manifest,
            workspace_root=tmp_path / "workspace",
            user_config_root=tmp_path / "user-config",
        )
        audit = audit_plugin_artifact_security(
            manifest,
            dry_run,
            workspace_root=tmp_path / "workspace",
            user_config_root=tmp_path / "user-config",
        )

        assert manifest.command.executable == "vad"
        assert manifest.command.args == ("mcp", "run")
        assert manifest.command.env == {}
        assert all(not tool.approved_by_default for tool in manifest.tools)
        assert all(not value.startswith(("http://", "https://")) for value in manifest.command.args)
        assert dry_run.dry_run is True
        assert dry_run.writes_performed == 0
        assert {rollback.action for rollback in dry_run.rollback} == {"restore_or_remove"}
        assert audit.passed is True
        assert audit.findings == ()
