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

# ==============================================================================
# Evidence-Driven Autonomy Matrix (input.action == "evaluate_release_autonomy")
# ==============================================================================

default deny_stale_evidence = false
default deny_drift_detected = false
default deny_insufficient_assurance = false
default allow_autonomous_release = false
default require_human_approval = false
default require_restricted_authorization = false

_autonomy_assurance_rank = {
    "A1_BASIC": 1,
    "A2_INDEPENDENT": 2,
    "A3_ATTESTED": 3,
    "A4_RESTRICTED": 4,
}

_autonomy_required_rank = {
    "low": 1,
    "medium": 2,
    "high": 3,
    "critical": 4,
}

deny_stale_evidence {
    input.action == "evaluate_release_autonomy"
    input.evidence_fresh != true
}

deny_drift_detected {
    input.action == "evaluate_release_autonomy"
    input.stage in ["drift", "runtime"]
    input.drift_detected == true
}

deny_insufficient_assurance {
    input.action == "evaluate_release_autonomy"
    not _autonomy_assurance_rank[input.assurance_achieved]
}

deny_insufficient_assurance {
    input.action == "evaluate_release_autonomy"
    not _autonomy_required_rank[input.risk_tier]
}

deny_insufficient_assurance {
    input.action == "evaluate_release_autonomy"
    achieved := _autonomy_assurance_rank[input.assurance_achieved]
    required := _autonomy_required_rank[input.risk_tier]
    achieved < required
}

_deny_autonomy_drift_any_stage {
    input.action == "evaluate_release_autonomy"
    input.drift_detected == true
}

_deny_autonomy_unverified {
    input.action == "evaluate_release_autonomy"
    input.verification_passed == false
}

_deny_autonomy_self_approval {
    input.action == "evaluate_release_autonomy"
    is_string(input.builder_actor)
    input.builder_actor != ""
    is_string(input.verifier_actor)
    input.verifier_actor != ""
    input.risk_tier in ["medium", "high", "critical"]
    input.builder_actor == input.verifier_actor
}

_deny_autonomy_self_approval {
    input.action == "evaluate_release_autonomy"
    is_string(input.builder_actor)
    input.builder_actor != ""
    approver := input.human_approvers[_]
    approver == input.builder_actor
}

_deny_autonomy_invalid_stage {
    input.action == "evaluate_release_autonomy"
    not input.stage in ["build", "ci_cd", "deploy", "runtime", "drift"]
}

_autonomy_denied {
    deny_stale_evidence
}

_autonomy_denied {
    deny_drift_detected
}

_autonomy_denied {
    deny_insufficient_assurance
}

_autonomy_denied {
    _deny_autonomy_drift_any_stage
}

_autonomy_denied {
    _deny_autonomy_unverified
}

_autonomy_denied {
    _deny_autonomy_self_approval
}

_autonomy_denied {
    _deny_autonomy_invalid_stage
}

allow_autonomous_release {
    input.action == "evaluate_release_autonomy"
    not _autonomy_denied
    input.risk_tier == "low"
    input.verification_passed == true
}

allow_autonomous_release {
    input.action == "evaluate_release_autonomy"
    not _autonomy_denied
    input.risk_tier == "medium"
    input.verification_passed == true
    input.independent_verifier == true
}

require_human_approval {
    input.action == "evaluate_release_autonomy"
    not _autonomy_denied
    input.risk_tier == "high"
}

require_restricted_authorization {
    input.action == "evaluate_release_autonomy"
    not _autonomy_denied
    input.risk_tier == "critical"
}

allow {
    input.action == "evaluate_release_autonomy"
    allow_autonomous_release
}
