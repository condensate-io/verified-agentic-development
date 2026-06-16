import pytest
from vad.policy.engine import PolicyEngine
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
