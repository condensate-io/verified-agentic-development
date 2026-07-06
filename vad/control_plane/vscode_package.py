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


class VSCodePackage(BaseModel):
    manifest: VADPluginManifest
    mcp_config: dict[str, Any]
    workspace_tasks: dict[str, Any]
    dashboard_url: str = Field(min_length=1)
    manual_fallback: str = Field(min_length=1)


def build_vscode_package() -> VSCodePackage:
    mcp_config = vscode_mcp_config()
    workspace_tasks = vscode_workspace_tasks()
    manifest = VADPluginManifest(
        plugin_id="vad-vscode-local",
        target_client=PluginTargetClient.VS_CODE,
        version="1.0.0",
        command=PluginCommand(executable="vad", args=("mcp", "run")),
        config_paths=(
            PluginConfigPath(scope=PluginConfigScope.WORKSPACE, path=".vscode/mcp.json"),
            PluginConfigPath(scope=PluginConfigScope.WORKSPACE, path=".vscode/tasks.json"),
            PluginConfigPath(scope=PluginConfigScope.USER, path="vscode/mcp.json"),
        ),
        permissions=(
            PluginPermission(
                name="mcp_stdio",
                reason="Expose the local VAD MCP stdio server to VS Code.",
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
            "config/.vscode/mcp.json": payload_digest(mcp_config),
            "config/.vscode/tasks.json": payload_digest(workspace_tasks),
        },
    )
    return VSCodePackage(
        manifest=manifest,
        mcp_config=mcp_config,
        workspace_tasks=workspace_tasks,
        dashboard_url="http://127.0.0.1:8080",
        manual_fallback="vad mcp run",
    )


def vscode_mcp_config() -> dict[str, Any]:
    return {
        "servers": {
            "vad": {
                "type": "stdio",
                "command": "vad",
                "args": ["mcp", "run"],
                "env": {"VAD_LIVE_SERVICES": "disabled"},
            }
        }
    }


def vscode_workspace_tasks() -> dict[str, Any]:
    return {
        "version": "2.0.0",
        "tasks": [
            {
                "label": "VAD: Serve dashboard",
                "type": "shell",
                "command": "vad",
                "args": [
                    "ui",
                    "serve",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    "8080",
                    "--seed-level3-demo",
                ],
                "isBackground": True,
                "problemMatcher": [],
                "presentation": {
                    "reveal": "always",
                    "panel": "dedicated",
                },
            }
        ],
    }
