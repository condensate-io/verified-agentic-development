import pytest
from vad.policy.engine import PolicyEngine
from vad.policy.decisions import PolicyEvaluationError
from vad.guards.tools import ToolGuard
from vad.guards.models import ModelGuard
from vad.guards.memory import MemoryGuard

def test_default_deny():
    engine = PolicyEngine()
    input_data = {"action": "some_random_action"}
    # default allow is false, so it should return false or None
    is_allowed = engine.evaluate("policies/vad.rego", "data.vad.policy.allow", input_data)
    assert not is_allowed

def test_deny_builder_self_approval():
    engine = PolicyEngine()
    input_data = {
        "action": "build",
        "builder": "alice",
        "approver": "alice"
    }
    # Explicit deny
    is_denied = engine.evaluate("policies/vad.rego", "data.vad.policy.deny_builder_self_approval", input_data)
    assert is_denied is True
    
    # allow should be false
    is_allowed = engine.evaluate("policies/vad.rego", "data.vad.policy.allow", input_data)
    assert not is_allowed

def test_allow_valid_build():
    engine = PolicyEngine()
    input_data = {
        "action": "build",
        "builder": "alice",
        "approver": "bob"
    }
    is_allowed = engine.evaluate("policies/vad.rego", "data.vad.policy.allow", input_data)
    assert is_allowed is True

def test_evaluate_decision_allows_valid_build():
    engine = PolicyEngine()
    decision = engine.evaluate_decision("policies/vad.rego", {
        "action": "build",
        "builder": "alice",
        "approver": "bob"
    })

    assert decision.allow is True
    assert decision.reasons == ["policy allow rule matched"]
    assert decision.denials == []

def test_evaluate_decision_denies_self_approval():
    engine = PolicyEngine()
    decision = engine.evaluate_decision("policies/vad.rego", {
        "action": "build",
        "builder": "alice",
        "approver": "alice"
    })

    assert decision.allow is False
    assert "builder may not self-approve" in decision.denials

def test_evaluate_decision_opa_error_is_controlled():
    engine = PolicyEngine()

    with pytest.raises(PolicyEvaluationError):
        engine.evaluate_decision("policies/missing.rego", {"action": "build"})

def test_tool_guard_deny_unauthorized():
    guard = ToolGuard("policies/vad.rego")
    # shell should be denied
    assert guard.check_tool_access("shell") is False
    # network should be denied
    assert guard.check_tool_access("network") is False
    # read_file should be allowed (or rather, not denied by explicitly deny list, 
    # depending on how we handle default deny vs allow for tools. Let's say we just don't deny it)
    assert guard.check_tool_access("read_file") is True

def test_model_tier_enforcement():
    guard = ModelGuard("policies/vad.rego")
    # tier1 and tier2 are allowed
    assert guard.check_model_access("tier1") is True
    assert guard.check_model_access("tier2") is True
    # tier3 should be denied
    assert guard.check_model_access("tier3") is False

def test_memory_scope_enforcement():
    guard = MemoryGuard("policies/vad.rego")
    # agent_local is allowed
    assert guard.check_memory_access("agent_local") is True
    # global is denied
    assert guard.check_memory_access("global") is False

def test_deployment_policy_denies_production_apply_without_approval():
    engine = PolicyEngine()

    decision = engine.evaluate_deployment(
        action="deploy_apply",
        environment="production",
        telemetry_count=1,
        rollback_enabled=True,
    )

    assert decision.allow is False
    assert "production deployment requires approval" in decision.denials
    assert decision.requires_human is True

def test_deployment_policy_allows_staging_apply_and_dry_run():
    engine = PolicyEngine()

    dry_run = engine.evaluate_deployment(action="deploy_dry_run", environment="production")
    staging_apply = engine.evaluate_deployment(action="deploy_apply", environment="staging")

    assert dry_run.allow is True
    assert staging_apply.allow is True

def test_deployment_policy_enforces_telemetry_and_rollback_permission():
    engine = PolicyEngine()

    apply_decision = engine.evaluate_deployment(
        action="deploy_apply",
        environment="production",
        approval_ref="approval:1",
        telemetry_count=0,
        rollback_enabled=False,
    )
    rollback_decision = engine.evaluate_deployment(action="deploy_rollback", environment="production")
    approved_rollback = engine.evaluate_deployment(
        action="deploy_rollback",
        environment="production",
        rollback_approval_ref="approval:rollback-1",
    )

    assert apply_decision.allow is False
    assert "production deployment requires telemetry" in apply_decision.denials
    assert "production deployment requires rollback" in apply_decision.denials
    assert rollback_decision.allow is False
    assert "deployment rollback requires rollback approval" in rollback_decision.denials
    assert approved_rollback.allow is True
