import json
from pathlib import Path
import pytest
from vad.integrity.registry import IntegrityRegistry, RegistryEntry, RegistryVerdict


def test_registry_sections_and_verdicts(tmp_path):
    reg_file = tmp_path / "registry.json"
    reg = IntegrityRegistry(str(reg_file.as_posix()))

    # Register an agent
    agent_file = tmp_path / "custom_agent.py"
    agent_file.write_text("# Custom agent")
    e_agent = reg.register(
        str(agent_file.as_posix()),
        category="agents",
        verdict="ALLOW",
        report="Clean agent implementation",
        risk_score=10.0,
    )
    assert e_agent.category == "agents"
    assert e_agent.verdict == "ALLOW"

    # Register a hook
    hook_file = tmp_path / "pre_tool.sh"
    hook_file.write_text("# Hook")
    e_hook = reg.register(
        str(hook_file.as_posix()),
        category="hooks",
        verdict="CONDITIONAL",
        report="Requires container sandboxing",
        risk_score=45.0,
        conditions=["container_isolation_active"],
    )
    assert e_hook.category == "hooks"
    assert "container_isolation_active" in e_hook.conditions

    # Register a command with REJECT
    cmd_file = tmp_path / "dangerous_command.sh"
    cmd_file.write_text("rm -rf /")
    reg.register(
        str(cmd_file.as_posix()),
        category="commands",
        verdict="REJECT",
        report="Contains dangerous wipe instruction",
        risk_score=95.0,
    )

    # Verify query by section
    agents = reg.get_section("agents")
    hooks = reg.get_section("hooks")
    commands = reg.get_section("commands")
    assert str(agent_file.as_posix()) in agents
    assert str(hook_file.as_posix()) in hooks
    assert str(cmd_file.as_posix()) in commands

    # REJECT artifact must fail verify_safe
    valid, err = reg.verify_safe(str(cmd_file.as_posix()))
    assert valid is False
    assert "REJECT" in err

    # Reload from disk and verify structured sections persisted
    reg_reloaded = IntegrityRegistry(str(reg_file.as_posix()))
    with open(reg_file, "r") as f:
        raw_data = json.load(f)
    assert "agents" in raw_data
    assert "hooks" in raw_data
    assert "commands" in raw_data
    assert "skills" in raw_data
    assert len(reg_reloaded.get_section("agents")) == 1


def test_registry_backward_compatibility(tmp_path):
    reg_file = tmp_path / "legacy_registry.json"
    dummy_file = tmp_path / "skill.md"
    dummy_file.write_text("skill")
    
    from vad.integrity.registry import IntegrityRegistry
    sha = IntegrityRegistry.compute_sha256(str(dummy_file))

    # Save legacy flat dict format
    legacy_data = {
        str(dummy_file.as_posix()): {
            "path": str(dummy_file.as_posix()),
            "sha256": sha,
            "registered_at": "2026-01-01T00:00:00Z",
            "category": "skill",
            "size_bytes": 5,
        }
    }
    with open(reg_file, "w") as f:
        json.dump(legacy_data, f)

    reg = IntegrityRegistry(str(reg_file.as_posix()))
    assert len(reg.get_all()) == 1
    # Migrated into skills section
    skills = reg.get_section("skills")
    assert str(dummy_file.as_posix()) in skills
