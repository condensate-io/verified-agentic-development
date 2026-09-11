import pytest
from vad.harness.targets.claude_code_target import CanonicalHarnessSpec, CanonicalAgent, GovernanceRule
from vad.harness.targets.cursor_target import CursorTarget


def test_cursor_target_rendering():
    spec = CanonicalHarnessSpec(
        project_name="VoiceAgentic",
        description="Governed workspace for voice telephony",
        agents=[
            CanonicalAgent(name="cursor-coder", role="Fullstack Engineer", allowed_tools=["read", "edit"])
        ],
        rules=[
            GovernanceRule(id="RULE_ISOLATION", name="ContainerIsolation", description="Execute in docker")
        ],
    )

    target = CursorTarget()
    rendered = target.render(spec)

    assert "AGENTS.md" in rendered
    assert ".cursor/rules/vad_governance.mdc" in rendered
    assert ".cursor/rules/rule-isolation.mdc" in rendered

    agents_content = rendered["AGENTS.md"]
    assert "VoiceAgentic" in agents_content
    assert "cursor-coder" in agents_content

    main_rule = rendered[".cursor/rules/vad_governance.mdc"]
    assert "alwaysApply: true" in main_rule
    assert "RULE_ISOLATION" in main_rule
    assert "Strict container isolation" in main_rule
