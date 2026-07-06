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


class WindsurfPackage(BaseModel):
    manifest: VADPluginManifest
    mcp_config: dict[str, Any]
    rule_files: dict[str, str]
    workflow_files: dict[str, str]
    manual_fallback: str = Field(min_length=1)


def build_windsurf_package() -> WindsurfPackage:
    mcp_config = windsurf_mcp_config()
    rule_files = windsurf_rule_files()
    workflow_files = windsurf_workflow_files()
    manifest = VADPluginManifest(
        plugin_id="vad-windsurf-local",
        target_client=PluginTargetClient.WINDSURF,
        version="1.0.0",
        command=PluginCommand(executable="vad", args=("mcp", "run")),
        config_paths=(
            PluginConfigPath(scope=PluginConfigScope.USER, path=".codeium/windsurf/mcp_config.json"),
            PluginConfigPath(scope=PluginConfigScope.WORKSPACE, path=".devin/rules/vad-builder.md"),
            PluginConfigPath(scope=PluginConfigScope.WORKSPACE, path=".devin/rules/vad-verifier.md"),
            PluginConfigPath(scope=PluginConfigScope.WORKSPACE, path=".devin/rules/vad-auditor.md"),
            PluginConfigPath(scope=PluginConfigScope.WORKSPACE, path=".windsurf/workflows/vad-verify.md"),
        ),
        permissions=(
            PluginPermission(
                name="mcp_stdio",
                reason="Expose the local VAD MCP stdio server to Windsurf Cascade.",
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
            PluginPrompt(prompt_id="vad-windsurf-builder", role="builder", path=".devin/rules/vad-builder.md"),
            PluginPrompt(prompt_id="vad-windsurf-verifier", role="verifier", path=".devin/rules/vad-verifier.md"),
            PluginPrompt(prompt_id="vad-windsurf-auditor", role="auditor", path=".devin/rules/vad-auditor.md"),
        ),
        checksums={
            "config/.codeium/windsurf/mcp_config.json": payload_digest(mcp_config),
            **{f".devin/rules/vad-{role}.md": payload_digest(text) for role, text in rule_files.items()},
            **{f".windsurf/workflows/{name}.md": payload_digest(text) for name, text in workflow_files.items()},
        },
    )
    return WindsurfPackage(
        manifest=manifest,
        mcp_config=mcp_config,
        rule_files=rule_files,
        workflow_files=workflow_files,
        manual_fallback="vad mcp run",
    )


def windsurf_mcp_config() -> dict[str, Any]:
    return {
        "mcpServers": {
            "vad": {
                "command": "vad",
                "args": ["mcp", "run"],
                "env": {"VAD_LIVE_SERVICES": "disabled"},
            }
        }
    }


def windsurf_rule_files() -> dict[str, str]:
    return {
        "builder": """---
trigger: model_decision
description: Use VAD builder workflow, tracker evidence, and local MCP tools for implementation slices.
---

# VAD Builder

- Read the active VAD plan, tracker, and proof obligations before editing.
- Keep each slice complete and avoid placeholder code or partial write paths.
- Use VAD MCP tools for repository assessment and evidence inspection.
- Treat high-risk repository mutation as unavailable unless explicitly approved for the run.
""",
        "verifier": """---
trigger: model_decision
description: Verify VAD proof obligations and report exact evidence.
---

# VAD Verifier

- Run the tracker-stated verification commands.
- Inspect generated evidence and control-plane events.
- Report exact command output, artifact paths, and failures.
- Treat missing proof, skipped write paths, or placeholder behavior as incomplete.
""",
        "auditor": """---
trigger: model_decision
description: Audit VAD policy, signatures, retrospectives, recovery notes, and local-only defaults.
---

# VAD Auditor

- Review policy decisions, signature evidence, retrospectives, and recovery notes before accepting completion.
- Confirm generated configs do not embed credentials or non-local endpoints.
- Confirm high-risk tools are not approved by default.
""",
    }


def windsurf_workflow_files() -> dict[str, str]:
    return {
        "vad-verify": """# VAD Verify

Use this workflow when a VAD implementation slice is ready for verification.

1. Read the active tracker item and its required checks.
2. Run the focused proof command for the touched package or integration.
3. Inspect generated evidence and control-plane events for the run.
4. Run the full Docker-under-WSL gate before marking the tracker item complete.
5. Summarize the commands, pass/fail counts, and any remaining blockers.
"""
    }
