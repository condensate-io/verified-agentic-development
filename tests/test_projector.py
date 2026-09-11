import pytest
import yaml
from pathlib import Path
from vad.harness.projector import MultiHarnessProjector, main
from vad.harness.targets.claude_code_target import CanonicalHarnessSpec, CanonicalAgent, GovernanceRule


def test_projector_all_targets(tmp_path):
    spec = CanonicalHarnessSpec(
        project_name="MultiHarnessApp",
        description="Unified governed AI workspace",
        agents=[CanonicalAgent(name="agent-1", role="Developer", allowed_tools=["pytest"])],
        rules=[GovernanceRule(id="G1", name="RuleOne", description="First rule")],
    )

    projector = MultiHarnessProjector()
    written = projector.project(spec, target_name="all", output_root=tmp_path)

    assert "CLAUDE.md" in written
    assert ".claude/settings.json" in written
    assert "AGENTS.md" in written
    assert ".cursor/rules/vad_governance.mdc" in written
    assert ".github/copilot-instructions.md" in written

    assert (tmp_path / "CLAUDE.md").exists()
    assert (tmp_path / ".claude/settings.json").exists()
    assert (tmp_path / "AGENTS.md").exists()
    assert (tmp_path / ".github/copilot-instructions.md").exists()


def test_projector_single_target(tmp_path):
    spec = CanonicalHarnessSpec(
        project_name="CopilotOnly",
        rules=[GovernanceRule(id="G2", name="RuleTwo", description="Second rule")],
    )
    projector = MultiHarnessProjector()
    written = projector.project(spec, target_name="copilot", output_root=tmp_path)

    assert len(written) == 1
    assert ".github/copilot-instructions.md" in written
    assert not (tmp_path / "CLAUDE.md").exists()


def test_projector_cli(tmp_path):
    out_dir = tmp_path / "out"
    ret = main(["--target", "claude", "--output", str(out_dir)])
    assert ret == 0
    assert (out_dir / "CLAUDE.md").exists()
