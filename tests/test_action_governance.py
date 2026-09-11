import pytest
from vad.policy.action_governance import ActionGovernanceEngine, GovernanceDecision
from vad.policy.autonomy_policy import (
    AutonomyPolicyEvaluator,
    AutonomyPolicyResource,
    HumanInTheLoopTrigger,
)
from vad.policy.llm_hooks import ConfigurableLLMPolicyProvider


@pytest.fixture
def governance_engine():
    policy = AutonomyPolicyResource(
        allowed_tools=["read_file", "write_file", "bash", "deploy_app"],
        blocked_tools=["git_push_force"],
        hitl_triggers=[
            HumanInTheLoopTrigger(action_pattern="deploy_.*", reason="Deployment requires human review")
        ],
    )
    return ActionGovernanceEngine(
        policy_evaluator=AutonomyPolicyEvaluator(policy),
        llm_provider=ConfigurableLLMPolicyProvider(),
    )


def test_action_governance_phase_1_static_block(governance_engine):
    res = governance_engine.evaluate_action("git_push_force", {})
    assert res.decision == GovernanceDecision.DENY
    assert res.phase == "static_rule"
    assert "Phase 1 Static Block" in res.reason


def test_action_governance_phase_1_static_escalate(governance_engine):
    res = governance_engine.evaluate_action("deploy_app", {"env": "prod"})
    assert res.decision == GovernanceDecision.ASK
    assert res.phase == "static_rule"


def test_action_governance_phase_2_llm_safe(governance_engine):
    res = governance_engine.evaluate_action("bash", {"command": "pytest tests/test_smoke.py"})
    assert res.decision == GovernanceDecision.ALLOW
    assert res.phase == "llm_evaluator"


def test_action_governance_phase_2_llm_destructive(governance_engine):
    res = governance_engine.evaluate_action("bash", {"command": "rm -rf /"})
    assert res.decision == GovernanceDecision.DENY
    assert res.phase == "llm_evaluator"
    assert "High-risk command detected" in res.reason
