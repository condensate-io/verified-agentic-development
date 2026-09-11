"""Settings Injector: Idempotently configures and merges VAD lifecycle hooks and MCP tools into .claude/settings.json."""

import json
from pathlib import Path
from typing import Any, Dict, List, Optional


class SettingsInjector:
    """Manages injection of VAD governance hooks and MCP servers into Claude Code settings."""

    HOOK_COMMANDS = {
        "PreToolUse": "python3 -m vad.plugins.claude_code.hooks.pre_tool_use",
        "PostToolUse": "python3 -m vad.plugins.claude_code.hooks.post_tool_use",
        "Stop": "python3 -m vad.plugins.claude_code.hooks.stop",
        "SubagentStop": "python3 -m vad.plugins.claude_code.hooks.subagent_stop",
        "Notification": "python3 -m vad.plugins.claude_code.hooks.notification",
        "UserPromptSubmit": "python3 -m vad.plugins.claude_code.hooks.user_prompt_submit",
        "SessionStart": "python3 -m vad.plugins.claude_code.hooks.session_start",
    }

    def __init__(self, settings_path: Optional[Path] = None):
        self.settings_path = Path(settings_path) if settings_path else Path(".claude/settings.json")

    def build_vad_hooks_config(self) -> Dict[str, Any]:
        hooks = {}
        for hook_name, cmd in self.HOOK_COMMANDS.items():
            hooks[hook_name] = [
                {
                    "type": "command",
                    "command": cmd,
                    "id": f"vad-{hook_name.lower()}",
                }
            ]
        return hooks

    def build_vad_mcp_config(self) -> Dict[str, Any]:
        return {
            "vad": {
                "command": "python3",
                "args": ["-m", "vad.plugins.claude_code.mcp_integration"],
            }
        }

    def inject(self) -> Dict[str, Any]:
        """Reads existing settings, merges VAD hooks and MCP server idempotently, and writes back."""
        data: Dict[str, Any] = {}
        if self.settings_path.exists():
            try:
                data = json.loads(self.settings_path.read_text(encoding="utf-8"))
            except Exception:
                data = {}

        # 1. Merge hooks
        if "hooks" not in data or not isinstance(data["hooks"], dict):
            data["hooks"] = {}

        vad_hooks = self.build_vad_hooks_config()
        for hook_name, hook_list in vad_hooks.items():
            if hook_name not in data["hooks"]:
                data["hooks"][hook_name] = hook_list
            else:
                existing = data["hooks"][hook_name]
                if isinstance(existing, list):
                    # Filter out any prior vad hooks to remain idempotent
                    clean_existing = [h for h in existing if isinstance(h, dict) and not str(h.get("id", "")).startswith("vad-")]
                    clean_existing.extend(hook_list)
                    data["hooks"][hook_name] = clean_existing
                else:
                    data["hooks"][hook_name] = hook_list

        # 2. Merge MCP servers
        if "mcpServers" not in data or not isinstance(data["mcpServers"], dict):
            data["mcpServers"] = {}
        data["mcpServers"].update(self.build_vad_mcp_config())

        # 3. Write back
        self.settings_path.parent.mkdir(parents=True, exist_ok=True)
        self.settings_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        return data

    def verify(self) -> bool:
        """Verifies whether all 7 VAD hooks are present in settings.json."""
        if not self.settings_path.exists():
            return False
        try:
            data = json.loads(self.settings_path.read_text(encoding="utf-8"))
        except Exception:
            return False

        hooks = data.get("hooks", {})
        for hook_name in self.HOOK_COMMANDS:
            if hook_name not in hooks:
                return False
            configured = hooks[hook_name]
            if not isinstance(configured, list):
                return False
            has_vad = any(
                isinstance(h, dict) and (h.get("id") == f"vad-{hook_name.lower()}" or "vad.plugins" in str(h.get("command", "")))
                for h in configured
            )
            if not has_vad:
                return False

        return True

    def remove(self) -> Dict[str, Any]:
        """Removes VAD hook registrations cleanly from settings.json."""
        if not self.settings_path.exists():
            return {}

        data = json.loads(self.settings_path.read_text(encoding="utf-8"))
        hooks = data.get("hooks", {})
        for hook_name in list(hooks.keys()):
            if isinstance(hooks[hook_name], list):
                hooks[hook_name] = [
                    h for h in hooks[hook_name]
                    if isinstance(h, dict) and not str(h.get("id", "")).startswith("vad-")
                ]
                if not hooks[hook_name]:
                    del hooks[hook_name]

        if "mcpServers" in data and "vad" in data["mcpServers"]:
            del data["mcpServers"]["vad"]

        self.settings_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        return data
