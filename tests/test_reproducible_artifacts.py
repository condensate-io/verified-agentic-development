from pathlib import Path
import json

import pytest

from vad.control_plane.artifacts import build_reproducible_artifacts


def test_reproducible_artifact_builder_writes_deterministic_paths_and_digest_report(tmp_path):
    first = build_reproducible_artifacts(tmp_path / "first")
    second = build_reproducible_artifacts(tmp_path / "second")

    first_paths = [artifact.path for artifact in first.artifacts]
    second_paths = [artifact.path for artifact in second.artifacts]
    assert first_paths == second_paths
    assert first.artifact_count == len(first_paths)
    assert first.secret_scan_passed is True
    assert "core/verified-agentic-development-0.1.0/package.json" in first_paths
    assert "schemas/vad-plugin-manifest.schema.json" in first_paths
    assert "schemas/eip.schema.json" in first_paths
    assert "plugins/vad-codex-local/1.0.0/manifest.json" in first_paths
    assert "plugins/vad-generic-mcp/1.0.0/manifest.json" in first_paths

    first_digests = {artifact.path: artifact.digest for artifact in first.artifacts}
    second_digests = {artifact.path: artifact.digest for artifact in second.artifacts}
    assert first_digests == second_digests

    report = json.loads((tmp_path / "first" / "digest-report.json").read_text(encoding="utf-8"))
    assert report["artifact_count"] == first.artifact_count
    assert report["report_digest"] == first.report_digest
    assert report["secret_scan_passed"] is True


def test_reproducible_artifact_builder_outputs_reviewable_manifest_json(tmp_path):
    report = build_reproducible_artifacts(tmp_path / "artifacts")
    manifest_path = tmp_path / "artifacts" / "plugins" / "vad-codex-local" / "1.0.0" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert manifest["plugin_id"] == "vad-codex-local"
    assert manifest["version"] == "1.0.0"
    assert manifest["command"] == {"args": ["mcp", "run"], "env": {}, "executable": "vad"}
    assert all(not tool["approved_by_default"] for tool in manifest["tools"])
    assert any(artifact.path == "plugins/vad-codex-local/1.0.0/manifest.json" for artifact in report.artifacts)


def test_reproducible_artifact_builder_rejects_secret_markers(tmp_path):
    project_root = tmp_path / "project"
    (project_root / "schemas").mkdir(parents=True)
    (project_root / "pyproject.toml").write_text('[project]\nversion = "9.9.9"\n', encoding="utf-8")
    (project_root / "schemas" / "vad-plugin-manifest.schema.json").write_text('{"title":"ok"}', encoding="utf-8")
    (project_root / "schemas" / "eip.schema.json").write_text('{"title":"token=leak"}', encoding="utf-8")

    with pytest.raises(ValueError, match="secret marker"):
        build_reproducible_artifacts(tmp_path / "artifacts", project_root=project_root)
