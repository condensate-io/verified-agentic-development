"""Claude Code Projection Target: Renders canonical governance models into CLAUDE.md and .claude/settings.json."""

import json
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class GovernanceRule(BaseModel):
    id: str
    name: str
    description: str
    severity: str = "error"


class CanonicalAgent(BaseModel):
    name: str
    role: str
    allowed_tools: List[str] = Field(default_factory=list)
    rules: List[str] = Field(default_factory=list)


class CanonicalHarnessSpec(BaseModel):
    project_name: str
    description: str = "Enterprise codebase governed by VAD"
    agents: List[CanonicalAgent] = Field(default_factory=list)
    rules: List[GovernanceRule] = Field(default_factory=list)


class ClaudeCodeTarget:
    """Projects canonical governance specs into Claude Code compatible artifacts."""

    def render(self, spec: CanonicalHarnessSpec) -> Dict[str, str]:
        claude_md = self.render_claude_md(spec)
        settings_json = self.render_settings_json(spec)
        return {
            "CLAUDE.md": claude_md,
            ".claude/settings.json": settings_json,
        }

    def render_claude_md(self, spec: CanonicalHarnessSpec) -> str:
        lines = [
            f"# CLAUDE.md — {spec.project_name}",
            f"> {spec.description}",
            "",
            "## Verified Agentic Development (VAD) Rules",
            "This repository operates under strict VAD Level 3 governance.",
            "",
            "### Enforced Governance Rules",
        ]
        for rule in spec.rules:
            lines.append(f"- **[{rule.id}] {rule.name}**: {rule.description} (Severity: `{rule.severity}`)")

        lines.extend([
            "",
            "### Authorized Agents & Roles",
        ])
        for agent in spec.agents:
            tools_str = ", ".join(agent.allowed_tools) or "standard"
            lines.append(f"- **{agent.name}** ({agent.role}): Allowed tools: `[{tools_str}]`")

        lines.extend([
            "",
            "### Testing & Verification Mandate",
            "- Always verify changes in disposable Docker containers inside WSL.",
            "- Generate cryptographic verification evidence (`evidence/<task-id>.json`).",
            "- Direct autonomous git push is strictly prohibited without human operator signoff.",
        ])
        return "\n".join(lines)

    def render_settings_json(self, spec: CanonicalHarnessSpec) -> str:
        settings = {
            "version": "1.0",
            "projectName": spec.project_name,
            "hooks": {
                "PreToolUse": [{"type": "command", "command": "python3 -m vad.plugins.claude_code.hooks.pre_tool_use", "id": "vad-pretooluse"}],
                "PostToolUse": [{"type": "command", "command": "python3 -m vad.plugins.claude_code.hooks.post_tool_use", "id": "vad-posttooluse"}],
                "Stop": [{"type": "command", "command": "python3 -m vad.plugins.claude_code.hooks.stop", "id": "vad-stop"}],
                "SubagentStop": [{"type": "command", "command": "python3 -m vad.plugins.claude_code.hooks.subagent_stop", "id": "vad-subagentstop"}],
                "Notification": [{"type": "command", "command": "python3 -m vad.plugins.claude_code.hooks.notification", "id": "vad-notification"}],
                "UserPromptSubmit": [{"type": "command", "command": "python3 -m vad.plugins.claude_code.hooks.user_prompt_submit", "id": "vad-userpromptsubmit"}],
                "SessionStart": [{"type": "command", "command": "python3 -m vad.plugins.claude_code.hooks.session_start", "id": "vad-sessionstart"}],
            },
            "mcpServers": {
                "vad": {
                    "command": "python3",
                    "args": ["-m", "vad.plugins.claude_code.mcp_integration"],
                }
            },
        }
        return json.dumps(settings, indent=2)
