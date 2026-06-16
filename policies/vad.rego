package vad.policy

import future.keywords.in

# Default deny
default allow = false

# Rules for action approvals
allow {
    input.action == "build"
    input.builder != input.approver
    not forbidden_capability
}

allow {
    input.action == "use_model"
    input.model_tier in ["tier1", "tier2"]
}

allow {
    input.action == "memory_access"
    input.scope == "agent_local"
}

# Deny rules
forbidden_capability {
    input.action == "use_tool"
    input.tool in ["shell", "network"]
}

# Explicit denies to override any potential allow (if we had default allow, which we don't, but useful as a query point)
deny_builder_self_approval {
    input.action == "build"
    input.builder == input.approver
}

deny_unauthorized_tool {
    input.action == "use_tool"
    input.tool in ["shell", "network"]
}

deny_model_tier {
    input.action == "use_model"
    not input.model_tier in ["tier1", "tier2"]
}

deny_memory_scope {
    input.action == "memory_access"
    input.scope != "agent_local"
}
