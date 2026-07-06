from pathlib import Path
import json
import re

from vad.control_plane.claude_code_package import build_claude_code_package
from vad.control_plane.codex_package import build_codex_package
from vad.control_plane.cursor_package import build_cursor_package
from vad.control_plane.generic_mcp_package import build_generic_mcp_package
from vad.control_plane.opencode_package import build_opencode_package
from vad.control_plane.vscode_package import build_vscode_package
from vad.control_plane.windsurf_package import build_windsurf_package


ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "package-versioning.md"


def _core_version() -> str:
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^version = "([^"]+)"$', pyproject, re.MULTILINE)
    assert match is not None
    return match.group(1)


def test_package_versioning_policy_records_core_plugin_and_schema_versions():
    text = DOC.read_text(encoding="utf-8")
    packages = [
        build_generic_mcp_package(),
        build_claude_code_package(),
        build_codex_package(),
        build_vscode_package(),
        build_cursor_package(),
        build_windsurf_package(),
        build_opencode_package(),
    ]

    assert f"Core Python package | `{_core_version()}`" in text
    for package in packages:
        assert f"`{package.manifest.plugin_id}`" in text
        assert f"| `{package.manifest.version}` |" in text

    for schema_name in ["vad-plugin-manifest.schema.json", "eip.schema.json"]:
        assert f"schemas/{schema_name}" in text
        assert (ROOT / "schemas" / schema_name).exists()


def test_package_versioning_policy_defines_compatibility_rules():
    text = DOC.read_text(encoding="utf-8")

    for phrase in [
        "human-approved release decision",
        "SQLite schema versions",
        "PRAGMA user_version",
        "Keep local-only defaults",
        "Preserve `vad mcp run`, `vad control-plane serve`, and `vad local-os demo`",
        "high-risk tools not approved by default",
        "Breaking changes require a major version bump",
    ]:
        assert phrase in text


def test_package_schema_artifacts_expose_version_contracts():
    manifest_schema = json.loads((ROOT / "schemas" / "vad-plugin-manifest.schema.json").read_text(encoding="utf-8"))
    eip_schema = json.loads((ROOT / "schemas" / "eip.schema.json").read_text(encoding="utf-8"))

    assert manifest_schema["properties"]["version"]["pattern"].startswith("^\\d+\\.\\d+\\.\\d+")
    assert eip_schema["properties"]["version"]["pattern"] == "^\\d+\\.\\d+\\.\\d+$"
