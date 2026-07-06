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

allow {
    input.action == "use_tool"
    not forbidden_capability
}

allow {
    input.action == "delegate"
    not forbidden_capability
}

allow {
    input.action == "deploy_dry_run"
}

allow {
    input.action == "deploy_apply"
    input.environment != "production"
}

allow {
    input.action == "deploy_apply"
    input.environment == "production"
    input.approval_ref
    input.telemetry_count > 0
    input.rollback_enabled == true
}

allow {
    input.action == "deploy_rollback"
    input.rollback_approval_ref
}

# Deny rules
forbidden_capability {
    input.action == "use_tool"
    input.tool in ["shell", "network"]
}

forbidden_capability {
    input.action == "delegate"
    input.capability in ["shell", "network"]
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

deny_unauthorized_delegation {
    input.action == "delegate"
    input.capability in ["shell", "network"]
}

deny_deployment_apply_without_approval {
    input.action == "deploy_apply"
    input.environment == "production"
    not input.approval_ref
}

deny_deployment_missing_telemetry {
    input.action == "deploy_apply"
    input.environment == "production"
    input.telemetry_count == 0
}

deny_deployment_missing_rollback {
    input.action == "deploy_apply"
    input.environment == "production"
    input.rollback_enabled != true
}

deny_deployment_rollback_without_approval {
    input.action == "deploy_rollback"
    not input.rollback_approval_ref
}
