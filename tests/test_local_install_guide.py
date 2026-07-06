from pathlib import Path

from vad.control_plane.claude_code_package import build_claude_code_package
from vad.control_plane.codex_package import build_codex_package
from vad.control_plane.cursor_package import build_cursor_package
from vad.control_plane.generic_mcp_package import build_generic_mcp_package
from vad.control_plane.opencode_package import build_opencode_package
from vad.control_plane.vscode_package import build_vscode_package
from vad.control_plane.windsurf_package import build_windsurf_package


DOC = Path(__file__).resolve().parents[1] / "docs" / "local-install.md"


def test_local_install_guide_requires_no_cloud_service():
    text = DOC.read_text(encoding="utf-8")

    for phrase in [
        "does not require a cloud VAD service",
        "hosted dashboard",
        "remote MCP gateway",
        "live model credential",
        "marketplace package",
        "does not call cloud APIs or start paid model clients",
    ]:
        assert phrase in text


def test_local_install_guide_covers_manual_fallback_for_every_target():
    text = DOC.read_text(encoding="utf-8")
    package_ids = [
        build_generic_mcp_package().manifest.plugin_id,
        build_claude_code_package().manifest.plugin_id,
        build_codex_package().manifest.plugin_id,
        build_vscode_package().manifest.plugin_id,
        build_cursor_package().manifest.plugin_id,
        build_windsurf_package().manifest.plugin_id,
        build_opencode_package().manifest.plugin_id,
    ]

    assert "`verified-agentic-development`" in text
    for package_id in package_ids:
        assert f"`{package_id}`" in text
    for target in [
        "Generic MCP clients",
        "Claude Code",
        "Codex",
        "VS Code",
        "Cursor",
        "Windsurf",
        "OpenCode",
        "Antigravity",
    ]:
        assert target in text
    assert text.count("vad mcp run") >= 8


def test_local_install_guide_records_mcp_security_warnings():
    text = DOC.read_text(encoding="utf-8")

    for phrase in [
        "Local MCP servers run commands on the operator machine",
        "VAD_LIVE_SERVICES=disabled",
        "do not put API keys, tokens, passwords, signing secrets, private keys, or provider credentials",
        "keep high-risk tools approval-gated",
        "use temp-home or dry-run installer checks",
        "dry-run rollback metadata",
        "No current VAD command publishes packages",
        "writes real user config without a human operator",
    ]:
        assert phrase in text


def test_local_install_guide_links_artifact_and_publication_review():
    text = DOC.read_text(encoding="utf-8")

    assert "build_reproducible_artifacts" in text
    assert "digest-report.json" in text
    assert "docs/publication-decisions.md" in text
    assert "they do not approve publishing" in text
