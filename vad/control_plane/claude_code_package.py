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


class ClaudeCodePackage(BaseModel):
    manifest: VADPluginManifest
    mcp_config: dict[str, Any]
    role_prompts: dict[str, str]
    manual_fallback: str = Field(min_length=1)


def build_claude_code_package() -> ClaudeCodePackage:
    mcp_config = claude_code_mcp_config()
    role_prompts = claude_code_role_prompts()
    manifest = VADPluginManifest(
        plugin_id="vad-claude-code-local",
        target_client=PluginTargetClient.CLAUDE_CODE,
        version="1.0.0",
        command=PluginCommand(executable="vad", args=("mcp", "run")),
        config_paths=(
            PluginConfigPath(scope=PluginConfigScope.WORKSPACE, path=".mcp.json"),
            PluginConfigPath(scope=PluginConfigScope.USER, path="claude-code/vad-mcp.json"),
        ),
        permissions=(
            PluginPermission(
                name="mcp_stdio",
                reason="Expose the local VAD MCP stdio server to Claude Code.",
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
            PluginPrompt(prompt_id="vad-claude-builder", role="builder", path="prompts/claude-builder.md"),
            PluginPrompt(prompt_id="vad-claude-verifier", role="verifier", path="prompts/claude-verifier.md"),
            PluginPrompt(prompt_id="vad-claude-auditor", role="auditor", path="prompts/claude-auditor.md"),
        ),
        checksums={
            "config/.mcp.json": payload_digest(mcp_config),
            **{f"prompts/claude-{role}.md": payload_digest(text) for role, text in role_prompts.items()},
        },
    )
    return ClaudeCodePackage(
        manifest=manifest,
        mcp_config=mcp_config,
        role_prompts=role_prompts,
        manual_fallback="vad mcp run",
    )


def claude_code_mcp_config() -> dict[str, Any]:
    return {
        "mcpServers": {
            "vad": {
                "command": "vad",
                "args": ["mcp", "run"],
                "env": {"VAD_LIVE_SERVICES": "disabled"},
            }
        }
    }


def claude_code_role_prompts() -> dict[str, str]:
    return {
        "builder": (
            "Act as a VAD builder. Read the active EIP and proof plan before editing. "
            "Use VAD MCP tools for repository assessment and keep high-risk writes behind explicit approval."
        ),
        "verifier": (
            "Act as a VAD verifier. Run the stated proof obligations, inspect evidence, "
            "and report failures with the exact command and artifact path."
        ),
        "auditor": (
            "Act as a VAD auditor. Review policy decisions, signatures, retrospective learnings, "
            "and recovery evidence before accepting a run."
        ),
    }
