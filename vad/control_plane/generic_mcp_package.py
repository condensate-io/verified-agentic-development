from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from vad.control_plane.mcp_gateway import gateway_tool_registry
from vad.control_plane.plugins import (
    PluginCommand,
    PluginConfigPath,
    PluginConfigScope,
    PluginPermission,
    PluginTargetClient,
    PluginToolGrant,
    VADPluginManifest,
)
from vad.signing.local import payload_digest


class GenericMCPPackage(BaseModel):
    manifest: VADPluginManifest
    config_snippets: dict[str, dict[str, Any]]
    manual_fallback: str = Field(min_length=1)


def build_generic_mcp_package() -> GenericMCPPackage:
    snippets = generic_mcp_config_snippets()
    manifest = VADPluginManifest(
        plugin_id="vad-generic-mcp",
        target_client=PluginTargetClient.GENERIC_MCP,
        version="1.0.0",
        command=PluginCommand(executable="vad", args=("mcp", "run")),
        config_paths=(
            PluginConfigPath(scope=PluginConfigScope.WORKSPACE, path=".vad/mcp/generic-mcp.json"),
            PluginConfigPath(scope=PluginConfigScope.USER, path="vad/mcp/generic-mcp.json"),
        ),
        permissions=(
            PluginPermission(
                name="mcp_stdio",
                reason="Expose the local VAD MCP stdio server to generic MCP clients.",
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
        checksums={
            "config/mcpServers.json": payload_digest(snippets["mcpServers"]),
            "config/server.json": payload_digest(snippets["server"]),
        },
    )
    return GenericMCPPackage(
        manifest=manifest,
        config_snippets=snippets,
        manual_fallback="vad mcp run",
    )


def generic_mcp_config_snippets() -> dict[str, dict[str, Any]]:
    server = {
        "command": "vad",
        "args": ["mcp", "run"],
        "env": {"VAD_LIVE_SERVICES": "disabled"},
    }
    return {
        "mcpServers": {"mcpServers": {"vad": server}},
        "server": {"vad": server},
    }
