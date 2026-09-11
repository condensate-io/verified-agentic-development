import json
import pytest
from vad.harness.targets.claude_code_target import (
    ClaudeCodeTarget,
    CanonicalHarnessSpec,
    CanonicalAgent,
    GovernanceRule,
)


def test_claude_code_target_rendering():
    spec = CanonicalHarnessSpec(
        project_name="VoicePlatformCore",
        description="High throughput voice streaming backend",
        agents=[
            CanonicalAgent(name="telemetry-worker", role="Telemetry Engineer", allowed_tools=["read", "pytest"])
        ],
        rules=[
            GovernanceRule(id="RULE-1", name="NoDirectPush", description="Forbid automated git pushes", severity="error")
        ],
    )

    target = ClaudeCodeTarget()
    rendered = target.render(spec)

    assert "CLAUDE.md" in rendered
    assert ".claude/settings.json" in rendered

    md = rendered["CLAUDE.md"]
    assert "VoicePlatformCore" in md
    assert "RULE-1" in md
    assert "NoDirectPush" in md
    assert "telemetry-worker" in md

    settings = json.loads(rendered[".claude/settings.json"])
    assert "PreToolUse" in settings["hooks"]
    assert "vad" in settings["mcpServers"]
