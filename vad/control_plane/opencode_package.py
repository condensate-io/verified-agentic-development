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


class OpenCodePackage(BaseModel):
    manifest: VADPluginManifest
    opencode_config: dict[str, Any]
    agent_files: dict[str, str]
    manual_fallback: str = Field(min_length=1)


def build_opencode_package() -> OpenCodePackage:
    opencode_config = opencode_jsonc_config()
    agent_files = opencode_agent_files()
    manifest = VADPluginManifest(
        plugin_id="vad-opencode-local",
        target_client=PluginTargetClient.OPENCODE,
        version="1.0.0",
        command=PluginCommand(executable="vad", args=("mcp", "run")),
        config_paths=(
            PluginConfigPath(scope=PluginConfigScope.WORKSPACE, path="opencode.jsonc"),
            PluginConfigPath(scope=PluginConfigScope.WORKSPACE, path=".opencode/agents/vad-builder.md"),
            PluginConfigPath(scope=PluginConfigScope.WORKSPACE, path=".opencode/agents/vad-verifier.md"),
            PluginConfigPath(scope=PluginConfigScope.WORKSPACE, path=".opencode/agents/vad-auditor.md"),
            PluginConfigPath(scope=PluginConfigScope.USER, path=".config/opencode/opencode.json"),
        ),
        permissions=(
            PluginPermission(
                name="mcp_stdio",
                reason="Expose the local VAD MCP stdio server to OpenCode.",
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
            PluginPrompt(prompt_id="vad-opencode-builder", role="builder", path=".opencode/agents/vad-builder.md"),
            PluginPrompt(prompt_id="vad-opencode-verifier", role="verifier", path=".opencode/agents/vad-verifier.md"),
            PluginPrompt(prompt_id="vad-opencode-auditor", role="auditor", path=".opencode/agents/vad-auditor.md"),
        ),
        checksums={
            "config/opencode.jsonc": payload_digest(opencode_config),
            **{f".opencode/agents/vad-{role}.md": payload_digest(text) for role, text in agent_files.items()},
        },
    )
    return OpenCodePackage(
        manifest=manifest,
        opencode_config=opencode_config,
        agent_files=agent_files,
        manual_fallback="vad mcp run",
    )


def opencode_jsonc_config() -> dict[str, Any]:
    return {
        "$schema": "https://opencode.ai/config.json",
        "mcp": {
            "vad": {
                "type": "local",
                "command": ["vad", "mcp", "run"],
                "enabled": True,
                "environment": {"VAD_LIVE_SERVICES": "disabled"},
            }
        },
        "permission": {
            "vad_*": "ask",
            "edit": "ask",
            "bash": "ask",
        },
        "agent": {
            "vad-builder": {
                "mode": "primary",
                "description": "Build complete VAD implementation slices with local MCP evidence.",
                "permission": {
                    "vad_*": "ask",
                    "vad_validate_eip": "allow",
                    "vad_repo_assess": "allow",
                    "vad_evidence_inspect": "allow",
                    "edit": "ask",
                    "bash": "ask",
                },
            },
            "vad-verifier": {
                "mode": "subagent",
                "description": "Verify VAD proof obligations and evidence without editing.",
                "permission": {
                    "vad_*": "ask",
                    "vad_validate_eip": "allow",
                    "vad_repo_assess": "allow",
                    "vad_evidence_inspect": "allow",
                    "vad_run_proofs": "ask",
                    "edit": "deny",
                    "bash": "ask",
                },
            },
            "vad-auditor": {
                "mode": "subagent",
                "description": "Audit VAD policy, signatures, retrospectives, and recovery evidence.",
                "permission": {
                    "vad_*": "ask",
                    "vad_validate_eip": "allow",
                    "vad_evidence_inspect": "allow",
                    "vad_retro_analyze": "ask",
                    "vad_submit_retrospective": "ask",
                    "edit": "deny",
                    "bash": "ask",
                },
            },
        },
    }


def opencode_agent_files() -> dict[str, str]:
    return {
        "builder": """---
description: Build complete VAD implementation slices with local MCP evidence.
mode: primary
permission:
  vad_*: ask
  vad_validate_eip: allow
  vad_repo_assess: allow
  vad_evidence_inspect: allow
  edit: ask
  bash: ask
---

# VAD Builder

Read the active tracker item, plan, and proof obligations before editing. Keep the slice complete, avoid placeholder code, and use VAD MCP tools for repository assessment and evidence inspection.

High-risk repository mutation must remain approval-gated; do not treat `vad_repo_patch` or `vad_repo_run` as pre-approved.
""",
        "verifier": """---
description: Verify VAD proof obligations and evidence without editing.
mode: subagent
permission:
  vad_*: ask
  vad_validate_eip: allow
  vad_repo_assess: allow
  vad_evidence_inspect: allow
  vad_run_proofs: ask
  edit: deny
  bash: ask
---

# VAD Verifier

Run the tracker-stated verification commands, inspect evidence and control-plane events, and report exact commands, artifacts, pass counts, and failures. Treat missing proof, skipped write paths, or placeholder behavior as incomplete.
""",
        "auditor": """---
description: Audit VAD policy, signatures, retrospectives, and recovery evidence.
mode: subagent
permission:
  vad_*: ask
  vad_validate_eip: allow
  vad_evidence_inspect: allow
  vad_retro_analyze: ask
  vad_submit_retrospective: ask
  edit: deny
  bash: ask
---

# VAD Auditor

Review policy decisions, signature evidence, retrospective learnings, and recovery notes before accepting completion. Confirm configs are local-only, contain no credentials, and do not approve high-risk tools by default.
""",
    }
