import pytest
from vad.integrity.integrity_checker import RuntimeIntegrityChecker
from vad.integrity.registry import IntegrityRegistry
from vad.plugins.claude_code.hooks.pre_tool_use import PreToolUseHook
from vad.policy.action_governance import ActionGovernanceEngine
from vad.policy.autonomy_policy import AutonomyPolicyEvaluator, AutonomyPolicyResource


@pytest.fixture
def pre_tool_hook(tmp_path):
    reg_file = tmp_path / "registry.json"
    reg = IntegrityRegistry(str(reg_file))

    # Register an approved script
    script = tmp_path / "valid_script.sh"
    script.write_text("echo 'hello'")
    reg.register(str(script))

    # Create a tampered script
    bad_script = tmp_path / "tampered.sh"
    bad_script.write_text("echo 'original'")
    reg.register(str(bad_script))
    bad_script.write_text("echo 'tampered'")

    checker = RuntimeIntegrityChecker(registry=reg, enforce=True)
    policy = AutonomyPolicyResource(
        allowed_tools=["read_file", "bash"],
        blocked_tools=["disallowed_tool"],
    )
    gov = ActionGovernanceEngine(policy_evaluator=AutonomyPolicyEvaluator(policy))

    return PreToolUseHook(governance_engine=gov, integrity_checker=checker), str(script), str(bad_script)


def test_pre_tool_use_allows_valid_call(pre_tool_hook):
    hook, valid_script, _ = pre_tool_hook
    payload = {
        "tool_name": "read_file",
        "tool_input": {"path": valid_script},
    }
    resp = hook.process(payload)
    assert resp.permissionDecision == "allow"
    assert resp.exit_code == 0


def test_pre_tool_use_blocks_tampered_file_integrity(pre_tool_hook):
    hook, _, bad_script = pre_tool_hook
    payload = {
        "tool_name": "bash",
        "tool_input": {"command": f"bash {bad_script}"},
    }
    resp = hook.process(payload)
    assert resp.permissionDecision == "deny"
    assert resp.exit_code == 2
    assert "Integrity Gate Block" in resp.reason


def test_pre_tool_use_blocks_disallowed_tool(pre_tool_hook):
    hook, _, _ = pre_tool_hook
    payload = {
        "tool_name": "disallowed_tool",
        "tool_input": {},
    }
    resp = hook.process(payload)
    assert resp.permissionDecision == "deny"
    assert resp.exit_code == 2
