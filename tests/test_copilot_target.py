import pytest
from vad.harness.targets.claude_code_target import CanonicalHarnessSpec, CanonicalAgent, GovernanceRule
from vad.harness.targets.copilot_target import CopilotTarget


def test_copilot_target_rendering():
    spec = CanonicalHarnessSpec(
        project_name="PlatformVoice",
        description="Voice engine infrastructure",
        agents=[
            CanonicalAgent(name="copilot-dev", role="Backend Specialist")
        ],
        rules=[
            GovernanceRule(id="VAD_DOCKER", name="ThrowawayContainer", description="Run in docker")
        ],
    )

    target = CopilotTarget()
    rendered = target.render(spec)

    assert ".github/copilot-instructions.md" in rendered
    content = rendered[".github/copilot-instructions.md"]
    assert "PlatformVoice" in content
    assert "Container Isolation" in content
    assert "ThrowawayContainer" in content
    assert "copilot-dev" in content
