from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from vad.control_plane.mcp_gateway import gateway_tool_registry
from vad.control_plane.plugins import (
    PluginCommand,
    PluginConfigPath,
    PluginConfigScope,
    PluginPermission,
    PluginPrompt,
    PluginTargetClient,
    PluginToolGrant,
    VADPluginManifest,
)
from vad.signing.local import payload_digest


class CursorPackage(BaseModel):
    manifest: VADPluginManifest
    mcp_config: dict[str, Any]
    rule_files: dict[str, str]
    manual_fallback: str = Field(min_length=1)


def build_cursor_package() -> CursorPackage:
    mcp_config = cursor_mcp_config()
    rule_files = cursor_rule_files()
    manifest = VADPluginManifest(
        plugin_id="vad-cursor-local",
        target_client=PluginTargetClient.CURSOR,
        version="1.0.0",
        command=PluginCommand(executable="vad", args=("mcp", "run")),
        config_paths=(
            PluginConfigPath(scope=PluginConfigScope.WORKSPACE, path=".cursor/mcp.json"),
            PluginConfigPath(scope=PluginConfigScope.WORKSPACE, path=".cursor/rules/vad-builder.mdc"),
            PluginConfigPath(scope=PluginConfigScope.WORKSPACE, path=".cursor/rules/vad-verifier.mdc"),
            PluginConfigPath(scope=PluginConfigScope.WORKSPACE, path=".cursor/rules/vad-auditor.mdc"),
            PluginConfigPath(scope=PluginConfigScope.USER, path=".cursor/mcp.json"),
        ),
        permissions=(
            PluginPermission(
                name="mcp_stdio",
                reason="Expose the local VAD MCP stdio server to Cursor.",
            ),
        ),
        tools=tuple(
            PluginToolGrant(
                name=tool.name,
                role=tool.required_role,
                high_risk=tool.high_risk,
                approved_by_default=False,
            )
            for tool in sorted(gateway_tool_registry().values(), key=lambda item: item.name)
        ),
        prompts=(
            PluginPrompt(prompt_id="vad-cursor-builder", role="builder", path=".cursor/rules/vad-builder.mdc"),
            PluginPrompt(prompt_id="vad-cursor-verifier", role="verifier", path=".cursor/rules/vad-verifier.mdc"),
            PluginPrompt(prompt_id="vad-cursor-auditor", role="auditor", path=".cursor/rules/vad-auditor.mdc"),
        ),
        checksums={
            "config/.cursor/mcp.json": payload_digest(mcp_config),
            **{f".cursor/rules/vad-{role}.mdc": payload_digest(text) for role, text in rule_files.items()},
        },
    )
    return CursorPackage(
        manifest=manifest,
        mcp_config=mcp_config,
        rule_files=rule_files,
        manual_fallback="vad mcp run",
    )


def cursor_mcp_config() -> dict[str, Any]:
    return {
        "mcpServers": {
            "vad": {
                "type": "stdio",
                "command": "vad",
                "args": ["mcp", "run"],
                "env": {"VAD_LIVE_SERVICES": "disabled"},
            }
        }
    }


def cursor_rule_files() -> dict[str, str]:
    return {
        "builder": """---
description: "Use VAD builder workflow and local MCP evidence for implementation slices"
alwaysApply: false
---

# VAD Builder

When implementing a VAD tracker item, read the active plan, tracker, and proof obligations before editing. Keep work small and complete, avoid placeholder code, and use VAD MCP tools for repository assessment and evidence inspection.

High-risk repository mutation remains gated: do not assume patching tools are available unless the run explicitly approves them.
""",
        "verifier": """---
description: "Verify VAD proof obligations and report exact evidence"
alwaysApply: false
---

# VAD Verifier

When verifying VAD work, run the tracker-stated proof commands, inspect evidence files, and report exact command output and artifact paths. Treat missing proof, skipped write paths, or placeholder behavior as incomplete.
""",
        "auditor": """---
description: "Audit VAD policy, signatures, retrospectives, and recovery evidence"
alwaysApply: false
---

# VAD Auditor

When auditing a VAD run, review policy decisions, signature evidence, retrospective learnings, and recovery notes before accepting completion. Confirm local-only defaults, no embedded credentials, and no high-risk tool approval by default.
""",
    }
