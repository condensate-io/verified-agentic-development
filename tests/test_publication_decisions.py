from pathlib import Path

from vad.control_plane.claude_code_package import build_claude_code_package
from vad.control_plane.codex_package import build_codex_package
from vad.control_plane.cursor_package import build_cursor_package
from vad.control_plane.generic_mcp_package import build_generic_mcp_package
from vad.control_plane.opencode_package import build_opencode_package
from vad.control_plane.vscode_package import build_vscode_package
from vad.control_plane.windsurf_package import build_windsurf_package


DOC = Path(__file__).resolve().parents[1] / "docs" / "publication-decisions.md"


def test_publication_decisions_cover_all_current_packages_and_targets():
    text = DOC.read_text(encoding="utf-8")
    package_ids = [
        "verified-agentic-development",
        build_generic_mcp_package().manifest.plugin_id,
        build_claude_code_package().manifest.plugin_id,
        build_codex_package().manifest.plugin_id,
        build_vscode_package().manifest.plugin_id,
        build_cursor_package().manifest.plugin_id,
        build_windsurf_package().manifest.plugin_id,
        build_opencode_package().manifest.plugin_id,
        "Antigravity generic fallback",
    ]

    for package_id in package_ids:
        assert f"`{package_id}`" in text or package_id in text

    for target in [
        "PyPI",
        "npm",
        "VS Code Marketplace",
        "OpenVSX",
        "Claude plugin marketplace",
        "Manual release",
    ]:
        assert target in text


def test_publication_decisions_keep_publishing_human_approved():
    text = DOC.read_text(encoding="utf-8")

    for phrase in [
        "They do not approve publishing.",
        "requires explicit human approval",
        "artifact paths and digest report",
        "passed secret scanning",
        "rollback or uninstall path",
        "requirements were rechecked",
        "No current VAD command uploads packages",
    ]:
        assert phrase in text


def test_publication_decisions_record_manual_fallback_without_marketplace_claims():
    text = DOC.read_text(encoding="utf-8")

    assert "| `vad-vscode-local` | not-applicable | deferred | deferred | deferred | not-applicable | approved-manual |" in text
    assert "| `vad-claude-code-local` | not-applicable | not-applicable | not-applicable | not-applicable | deferred | approved-manual |" in text
    assert "No first-class Antigravity package is generated" in text
    assert "marketplace acceptance as complete" in text
