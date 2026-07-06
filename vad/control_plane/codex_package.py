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


class CodexPackage(BaseModel):
    manifest: VADPluginManifest
    plugin_json: dict[str, Any]
    mcp_config: dict[str, Any]
    skill_guides: dict[str, str]
    manual_fallback: str = Field(min_length=1)


def build_codex_package() -> CodexPackage:
    plugin_json = codex_plugin_json()
    mcp_config = codex_mcp_config()
    skill_guides = codex_skill_guides()
    manifest = VADPluginManifest(
        plugin_id="vad-codex-local",
        target_client=PluginTargetClient.CODEX,
        version="1.0.0",
        command=PluginCommand(executable="vad", args=("mcp", "run")),
        config_paths=(
            PluginConfigPath(scope=PluginConfigScope.WORKSPACE, path=".codex-plugin/plugin.json"),
            PluginConfigPath(scope=PluginConfigScope.WORKSPACE, path=".mcp.json"),
            PluginConfigPath(scope=PluginConfigScope.USER, path="codex/vad/.codex-plugin/plugin.json"),
            PluginConfigPath(scope=PluginConfigScope.USER, path="codex/vad/.mcp.json"),
        ),
        permissions=(
            PluginPermission(
                name="mcp_stdio",
                reason="Expose the local VAD MCP stdio server to Codex.",
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
            PluginPrompt(prompt_id="vad-codex-builder", role="builder", path="skills/vad-builder/SKILL.md"),
            PluginPrompt(prompt_id="vad-codex-verifier", role="verifier", path="skills/vad-verifier/SKILL.md"),
            PluginPrompt(prompt_id="vad-codex-auditor", role="auditor", path="skills/vad-auditor/SKILL.md"),
        ),
        checksums={
            ".codex-plugin/plugin.json": payload_digest(plugin_json),
            ".mcp.json": payload_digest(mcp_config),
            **{f"skills/vad-{role}/SKILL.md": payload_digest(text) for role, text in skill_guides.items()},
        },
    )
    return CodexPackage(
        manifest=manifest,
        plugin_json=plugin_json,
        mcp_config=mcp_config,
        skill_guides=skill_guides,
        manual_fallback="vad mcp run",
    )


def codex_plugin_json() -> dict[str, Any]:
    return {
        "name": "vad-codex-local",
        "version": "1.0.0",
        "description": "Local VAD control-plane tools and role skills for Codex.",
        "author": {"name": "VAD Local"},
        "homepage": "https://openai.com/",
        "repository": "https://openai.com/",
        "license": "MIT",
        "keywords": ["vad", "mcp", "codex", "verification"],
        "skills": "./skills/",
        "mcpServers": "./.mcp.json",
        "interface": {
            "displayName": "VAD Local",
            "shortDescription": "Use local VAD control-plane tools from Codex",
            "longDescription": (
                "Use local VAD MCP tools and role-specific skill guidance for builder, "
                "verifier, and auditor workflows."
            ),
            "developerName": "VAD Local",
            "category": "Developer Tools",
            "capabilities": ["Interactive", "Read", "Write"],
            "websiteURL": "https://openai.com/",
            "privacyPolicyURL": "https://openai.com/policies/privacy-policy/",
            "termsOfServiceURL": "https://openai.com/policies/terms-of-use/",
            "defaultPrompt": [
                "Assess this repo with VAD",
                "Verify the active VAD run",
                "Audit VAD recovery evidence",
            ],
            "brandColor": "#1F6FEB",
            "screenshots": [],
        },
    }


def codex_mcp_config() -> dict[str, Any]:
    return {
        "mcpServers": {
            "vad": {
                "command": "vad",
                "args": ["mcp", "run"],
                "env": {"VAD_LIVE_SERVICES": "disabled"},
            }
        }
    }


def codex_skill_guides() -> dict[str, str]:
    return {
        "builder": """---
name: vad-builder
description: Build VAD implementation slices with local MCP evidence and no placeholder code.
---

# VAD Builder

Use this skill when implementing a VAD tracker item in Codex. Read the active plan, tracker, and local proof obligations before editing. Prefer small complete slices, keep high-risk repository writes behind explicit approval, and use VAD MCP tools to inspect repository state and control-plane evidence.

Before finishing, update durable handoff notes and run the stated verification gate.
""",
        "verifier": """---
name: vad-verifier
description: Verify VAD proof obligations, evidence files, and local control-plane events.
---

# VAD Verifier

Use this skill when checking whether a VAD implementation slice is complete. Run the tracker-stated tests, inspect generated evidence, and report exact commands, artifacts, and failures. Treat missing proof, skipped write paths, or placeholder behavior as incomplete.
""",
        "auditor": """---
name: vad-auditor
description: Audit VAD policy decisions, signatures, retrospectives, and recovery evidence.
---

# VAD Auditor

Use this skill when reviewing a VAD run for governance readiness. Check policy decisions, signatures, retrospective learnings, and recovery notes before accepting completion. Confirm dangerous tools are not approved by default and local-only defaults do not embed credentials or cloud endpoints.
""",
    }
