import pytest
from vad.policy.autonomy_policy import (
    AutonomyPolicyEvaluator,
    AutonomyPolicyResource,
    PolicyActionDecision,
)


def test_autonomy_policy_from_yaml():
    yaml_text = """
apiVersion: governance.vad.dev/v1
kind: AutonomyPolicy
metadata:
  name: team-telephony-policy
maturity_level: 3
scope:
  target_type: repo
  target_name: voice-agentic
allowed_tools:
  - read_file
  - write_file
  - pytest
blocked_tools:
  - git_push
  - rm_rf
max_cost_ceiling_usd: 5.0
max_token_ceiling: 50000
hitl_triggers:
  - action_pattern: "database_migration.*"
    reason: "Database migrations require DBA confirmation"
"""
    policy = AutonomyPolicyResource.from_yaml(yaml_text)
    assert policy.metadata["name"] == "team-telephony-policy"
    assert policy.maturity_level == 3
    assert policy.scope.target_name == "voice-agentic"

    evaluator = AutonomyPolicyEvaluator(policy)

    # Allowed tool
    dec, msg = evaluator.evaluate_tool("read_file")
    assert dec == PolicyActionDecision.ALLOW

    # Blocked tool
    dec, msg = evaluator.evaluate_tool("git_push")
    assert dec == PolicyActionDecision.DENY
    assert "explicitly blocked" in msg

    # Unlisted tool
    dec, msg = evaluator.evaluate_tool("curl")
    assert dec == PolicyActionDecision.DENY

    # HITL escalation trigger
    dec, msg = evaluator.evaluate_tool("database_migration_execute")
    assert dec == PolicyActionDecision.ESCALATE
    assert "require DBA confirmation" in msg


def test_autonomy_policy_budget_evaluation():
    policy = AutonomyPolicyResource(max_cost_ceiling_usd=2.0, max_token_ceiling=1000)
    evaluator = AutonomyPolicyEvaluator(policy)

    dec, msg = evaluator.evaluate_budget(tokens_used=500, cost_usd=1.0)
    assert dec == PolicyActionDecision.ALLOW

    dec, msg = evaluator.evaluate_budget(tokens_used=1500, cost_usd=1.0)
    assert dec == PolicyActionDecision.DENY
    assert "Token limit exceeded" in msg
