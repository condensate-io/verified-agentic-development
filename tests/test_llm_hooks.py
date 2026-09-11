import pytest
from vad.policy.llm_hooks import (
    ConfigurableLLMPolicyProvider,
    LLMPermissionDecision,
    LLMPolicyEvaluation,
)


def test_llm_policy_provider_allows_safe_action():
    provider = ConfigurableLLMPolicyProvider()
    res = provider.evaluate(
        action_name="run_tests",
        tool_input={"command": "pytest tests/test_smoke.py"},
        policy_context="Allow automated tests in throwaway environments",
    )
    assert res.decision == LLMPermissionDecision.ALLOW
    assert "complies with declared policy" in res.reason


def test_llm_policy_provider_denies_destructive_action():
    provider = ConfigurableLLMPolicyProvider()
    res = provider.evaluate(
        action_name="execute_terminal",
        tool_input={"command": "rm -rf /var/data"},
        policy_context="Strict isolation: no destructive file deletion",
    )
    assert res.decision == LLMPermissionDecision.DENY
    assert "High-risk command detected" in res.reason


def test_llm_policy_provider_asks_for_deployment():
    provider = ConfigurableLLMPolicyProvider()
    res = provider.evaluate(
        action_name="deploy_service",
        tool_input={"target": "gcp-production"},
        policy_context="Production changes require human gate",
    )
    assert res.decision == LLMPermissionDecision.ASK
    assert "requires human confirmation" in res.reason


def test_llm_policy_provider_custom_evaluator():
    def custom_fn(name, inp, ctx):
        return LLMPolicyEvaluation(
            decision=LLMPermissionDecision.ALLOW,
            reason="Custom provider approved",
            risk_assessment="None",
        )

    provider = ConfigurableLLMPolicyProvider(evaluator_fn=custom_fn)
    res = provider.evaluate("any_action", {}, "")
    assert res.decision == LLMPermissionDecision.ALLOW
    assert res.reason == "Custom provider approved"
