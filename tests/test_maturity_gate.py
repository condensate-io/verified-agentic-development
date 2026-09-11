import pytest
from vad.policy.autonomy_policy import AutonomyPolicyResource, PolicyScope
from vad.policy.maturity_gate import MaturityGate
from vad.policy.scope_resolver import ScopeResolver


@pytest.fixture
def maturity_gate():
    # Setup repo at Level 2 (Bounded / Local Verified)
    p_l2 = AutonomyPolicyResource(
        metadata={"name": "voice-core"},
        maturity_level=2,
        scope=PolicyScope(target_type="repo", target_name="voice-core"),
    )
    # Setup legacy repo at Level 0 (Read-only)
    p_l0 = AutonomyPolicyResource(
        metadata={"name": "legacy-monolith"},
        maturity_level=0,
        scope=PolicyScope(target_type="repo", target_name="legacy-monolith"),
    )

    resolver = ScopeResolver([p_l2, p_l0])
    return MaturityGate(scope_resolver=resolver)


def test_maturity_gate_permits_appropriate_actions(maturity_gate):
    # Level 2 repo can read files (L0) and run docker pytest (L2)
    res_read = maturity_gate.evaluate(repo_name="voice-core", action_name="read_file")
    assert res_read.permitted is True

    res_docker = maturity_gate.evaluate(repo_name="voice-core", action_name="run_docker_pytest")
    assert res_docker.permitted is True


def test_maturity_gate_blocks_advanced_action_for_low_maturity(maturity_gate):
    # Level 2 repo cannot autonomously confirm push (L4)
    res_push = maturity_gate.evaluate(repo_name="voice-core", action_name="confirm_push")
    assert res_push.permitted is False
    assert res_push.required_maturity == 4
    assert res_push.current_maturity == 2
    assert "requires maturity Level 4" in res_push.reason


def test_maturity_gate_blocks_write_on_l0_repo(maturity_gate):
    # Level 0 legacy repo cannot edit files (L1)
    res_edit = maturity_gate.evaluate(repo_name="legacy-monolith", action_name="edit_file")
    assert res_edit.permitted is False
    assert res_edit.current_maturity == 0
    assert res_edit.required_maturity == 1
