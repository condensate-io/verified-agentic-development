import json
import pytest
from pathlib import Path
from vad.plugins.claude_code.settings_injector import SettingsInjector


def test_settings_injector_fresh(tmp_path):
    settings_file = tmp_path / ".claude" / "settings.json"
    injector = SettingsInjector(settings_path=settings_file)

    assert injector.verify() is False
    res = injector.inject()

    assert settings_file.exists()
    assert injector.verify() is True
    assert "PreToolUse" in res["hooks"]
    assert "PostToolUse" in res["hooks"]
    assert "Stop" in res["hooks"]
    assert "SubagentStop" in res["hooks"]
    assert "Notification" in res["hooks"]
    assert "UserPromptSubmit" in res["hooks"]
    assert "SessionStart" in res["hooks"]
    assert "vad" in res["mcpServers"]


def test_settings_injector_idempotent(tmp_path):
    settings_file = tmp_path / ".claude" / "settings.json"
    injector = SettingsInjector(settings_path=settings_file)

    injector.inject()
    injector.inject()  # second run should not duplicate

    data = json.loads(settings_file.read_text(encoding="utf-8"))
    assert len(data["hooks"]["PreToolUse"]) == 1
    assert injector.verify() is True


def test_settings_injector_preserves_user_settings(tmp_path):
    settings_file = tmp_path / ".claude" / "settings.json"
    settings_file.parent.mkdir(parents=True)
    settings_file.write_text(json.dumps({
        "theme": "dark",
        "hooks": {
            "PreToolUse": [{"type": "command", "command": "echo custom", "id": "custom-hook"}]
        }
    }))

    injector = SettingsInjector(settings_path=settings_file)
    injector.inject()

    data = json.loads(settings_file.read_text(encoding="utf-8"))
    assert data["theme"] == "dark"
    pre_hooks = data["hooks"]["PreToolUse"]
    assert len(pre_hooks) == 2
    ids = [h["id"] for h in pre_hooks]
    assert "custom-hook" in ids
    assert "vad-pretooluse" in ids


def test_settings_injector_remove(tmp_path):
    settings_file = tmp_path / ".claude" / "settings.json"
    injector = SettingsInjector(settings_path=settings_file)
    injector.inject()
    assert injector.verify() is True

    injector.remove()
    assert injector.verify() is False
    data = json.loads(settings_file.read_text(encoding="utf-8"))
    assert "vad" not in data.get("mcpServers", {})
