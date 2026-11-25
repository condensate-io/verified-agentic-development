"""Tests for built-in Scope B Infrastructure and Operations domain packs."""
from __future__ import annotations

import pytest

from vad.adapters.domain_pack import (
    ArtifactProposalAdapter,
    DomainKind,
    DomainPack,
    DomainPackRegistry,
    DomainPolicyPack,
    ProofExecutorAdapter,
    evaluate_domain_proposal,
)
from vad.adapters.packs.infrastructure import (
    InfrastructurePolicyPack,
    InfrastructureProofExecutor,
    InfrastructureProposalAdapter,
    create_infrastructure_domain_pack,
)
from vad.adapters.packs.operations import (
    OperationsPolicyPack,
    OperationsProofExecutor,
    OperationsProposalAdapter,
    create_operations_domain_pack,
)
from vad.contracts.models import AssuranceLevel, RiskTier
from vad.release.decision import ReleaseOutcome


def test_builtin_infra_and_ops_packs_satisfy_contracts_and_register() -> None:
    infra_pack = create_infrastructure_domain_pack()
    ops_pack = create_operations_domain_pack()

    assert isinstance(infra_pack, DomainPack)
    assert isinstance(ops_pack, DomainPack)
    assert isinstance(infra_pack.proposal_adapter, InfrastructureProposalAdapter)
    assert isinstance(infra_pack.proof_executor, InfrastructureProofExecutor)
    assert isinstance(infra_pack.policy_pack, InfrastructurePolicyPack)
    assert isinstance(ops_pack.proposal_adapter, OperationsProposalAdapter)
    assert isinstance(ops_pack.proof_executor, OperationsProofExecutor)
    assert isinstance(ops_pack.policy_pack, OperationsPolicyPack)

    assert isinstance(infra_pack.proposal_adapter, ArtifactProposalAdapter)
    assert isinstance(infra_pack.proof_executor, ProofExecutorAdapter)
    assert isinstance(infra_pack.policy_pack, DomainPolicyPack)
    assert isinstance(ops_pack.proposal_adapter, ArtifactProposalAdapter)
    assert isinstance(ops_pack.proof_executor, ProofExecutorAdapter)
    assert isinstance(ops_pack.policy_pack, DomainPolicyPack)

    registry = DomainPackRegistry()
    registry.register(infra_pack)
    registry.register(ops_pack)
    assert registry.list_domains() == [DomainKind.INFRASTRUCTURE, DomainKind.OPERATIONS]


def test_infrastructure_risk_tiers_and_assurance_mapping() -> None:
    pack = create_infrastructure_domain_pack()
    adapter = pack.proposal_adapter
    policy = pack.policy_pack

    low_prop = adapter.build_proposal(
        target_resource="aws_s3_bucket.dev_scratch",
        summary="Update dev bucket tags",
        payload={"environment": "DEV", "resource_kind": "storage"},
        rollback_plan="terraform apply -var-file=prev.tfvars",
    )
    assert policy.default_risk_tier(low_prop) == RiskTier.LOW
    assert policy.required_assurance(low_prop) == AssuranceLevel.A1_BASIC

    med_prop = adapter.build_proposal(
        target_resource="k8s/deployment/api-staging",
        summary="Update staging replica count",
        payload={"environment": "Staging", "resource_kind": "k8s_deployment"},
        rollback_plan="kubectl rollout undo deployment/api-staging",
    )
    assert policy.default_risk_tier(med_prop) == RiskTier.MEDIUM
    assert policy.required_assurance(med_prop) == AssuranceLevel.A2_INDEPENDENT

    high_prop = adapter.build_proposal(
        target_resource="aws_security_group.prod_app",
        summary="Allow corporate subnet ingress",
        payload={"environment": "prod", "cidr_blocks": ["10.20.0.0/16"]},
        rollback_plan="terraform apply -var-file=prev.tfvars",
    )
    assert policy.default_risk_tier(high_prop) == RiskTier.HIGH
    assert policy.required_assurance(high_prop) == AssuranceLevel.A3_ATTESTED

    for critical_payload in (
        {"environment": "dev", "destructive": True},
        {"environment": "staging", "iam_privilege_escalation": True},
        {"environment": "production", "resource_kind": "iam"},
        {"environment": "production", "resource_kind": "kms"},
        {"environment": "production", "resource_kind": "network_firewall"},
        {"environment": "production", "resource_kind": "database_cluster"},
    ):
        crit_prop = adapter.build_proposal(
            target_resource="prod/critical-resource",
            summary="Critical infrastructure change",
            payload=critical_payload,
            rollback_plan="Restore from immutable snapshot",
        )
        assert policy.default_risk_tier(crit_prop) == RiskTier.CRITICAL
        assert policy.required_assurance(crit_prop) == AssuranceLevel.A4_RESTRICTED


def test_infrastructure_autonomy_lifecycle_across_all_tiers_and_apply_receipt() -> None:
    pack = create_infrastructure_domain_pack()
    adapter = pack.proposal_adapter
    assert isinstance(adapter, InfrastructureProposalAdapter)

    # LOW -> autonomous release
    low_prop = adapter.build_proposal(
        target_resource="k8s/configmap/dev-flags",
        summary="Set dev feature flag",
        payload={"environment": "sandbox"},
        rollback_plan="kubectl apply -f prev-configmap.yaml",
    )
    low_dec = evaluate_domain_proposal(
        pack,
        low_prop,
        run_id="run-infra-low",
        builder_actor="infra-builder",
        verifier_actor="infra-verifier",
    )
    assert low_dec.can_release is True
    assert low_dec.outcome == ReleaseOutcome.AUTONOMOUS_RELEASE
    assert low_dec.risk_tier == RiskTier.LOW

    # MEDIUM -> autonomous release with independent verifier
    med_prop = adapter.build_proposal(
        target_resource="aws_lb_target_group.staging",
        summary="Adjust staging health threshold",
        payload={"environment": "preprod"},
        rollback_plan="terraform apply -var-file=prev.tfvars",
    )
    med_dec = evaluate_domain_proposal(
        pack,
        med_prop,
        run_id="run-infra-med",
        builder_actor="infra-builder",
        verifier_actor="infra-verifier",
    )
    assert med_dec.can_release is True
    assert med_dec.outcome == ReleaseOutcome.AUTONOMOUS_RELEASE
    assert med_dec.risk_tier == RiskTier.MEDIUM

    # HIGH -> requires human guardian; rejects builder self-approval
    high_prop = adapter.build_proposal(
        target_resource="aws_security_group.prod_api",
        summary="Restrict prod ingress to internal CIDR",
        payload={"environment": "production", "cidr_blocks": ["10.0.0.0/8"]},
        rollback_plan="terraform apply -var-file=prev.tfvars",
    )
    high_blocked = evaluate_domain_proposal(
        pack,
        high_prop,
        run_id="run-infra-high-1",
        builder_actor="infra-builder",
        verifier_actor="infra-verifier",
    )
    assert high_blocked.can_release is False
    assert high_blocked.outcome == ReleaseOutcome.HUMAN_APPROVAL_REQUIRED

    high_self = evaluate_domain_proposal(
        pack,
        high_prop,
        run_id="run-infra-high-2",
        builder_actor="infra-builder",
        verifier_actor="infra-verifier",
        human_approvers=["infra-builder"],
    )
    assert high_self.can_release is False
    assert high_self.outcome == ReleaseOutcome.DENIED_POLICY

    high_approved = evaluate_domain_proposal(
        pack,
        high_prop,
        run_id="run-infra-high-3",
        builder_actor="infra-builder",
        verifier_actor="infra-verifier",
        human_approvers=["platform-guardian"],
    )
    assert high_approved.can_release is True
    assert high_approved.outcome == ReleaseOutcome.APPROVED_WITH_HUMAN

    with pytest.raises(PermissionError):
        adapter.apply_proposal(high_prop, approved_decision_id="   ")

    receipt = adapter.apply_proposal(high_prop, approved_decision_id=high_approved.decision_id)
    assert receipt == {
        "status": "applied",
        "domain": "infrastructure",
        "proposal_id": high_prop.proposal_id,
        "target_resource": "aws_security_group.prod_api",
        "approved_decision_id": high_approved.decision_id,
        "payload_digest": high_prop.payload_digest,
    }
    assert adapter.applied_history == [receipt]

    # CRITICAL -> requires dual human guardian + explicit_authorization_ref
    crit_prop = adapter.build_proposal(
        target_resource="aws_iam_role.prod_admin",
        summary="Rotate production IAM trust policy",
        payload={"environment": "production", "resource_kind": "iam"},
        rollback_plan="aws iam put-role-policy --cli-input-json file://prev.json",
    )
    crit_missing = evaluate_domain_proposal(
        pack,
        crit_prop,
        run_id="run-infra-crit-1",
        builder_actor="infra-builder",
        verifier_actor="infra-verifier",
        human_approvers=["sec-guardian-1"],
    )
    assert crit_missing.can_release is False
    assert crit_missing.outcome == ReleaseOutcome.RESTRICTED_AUTHORIZATION_REQUIRED

    crit_approved = evaluate_domain_proposal(
        pack,
        crit_prop,
        run_id="run-infra-crit-2",
        builder_actor="infra-builder",
        verifier_actor="infra-verifier",
        human_approvers=["sec-guardian-1", "sre-guardian-2"],
        explicit_authorization_ref="CAB-2026-401",
    )
    assert crit_approved.can_release is True
    assert crit_approved.outcome == ReleaseOutcome.APPROVED_RESTRICTED
    assert crit_approved.explicit_authorization_ref == "CAB-2026-401"


@pytest.mark.parametrize(
    ("payload", "rollback_plan", "expected_reason_fragment"),
    [
        ({"environment": "dev"}, "   ", "rollback_plan"),
        ({"environment": "production", "cidr_blocks": ["0.0.0.0/0"]}, "revert sg", "0.0.0.0/0"),
        ({"environment": "production", "cidr_blocks": ["::/0"]}, "revert sg", "::/0"),
        ({"environment": "staging", "iam_principals": ["*"]}, "revert iam", "principal"),
        ({"environment": "staging", "iam_actions": ["*"]}, "revert iam", "action"),
        (
            {"environment": "staging", "k8s_security_context": {"privileged": True}},
            "kubectl rollout undo",
            "privileged",
        ),
        (
            {
                "environment": "staging",
                "k8s_manifest": {
                    "spec": {
                        "template": {
                            "spec": {"containers": [{"name": "app", "securityContext": {"privileged": True}}]}
                        }
                    }
                },
            },
            "kubectl rollout undo",
            "privileged",
        ),
    ],
)
def test_infrastructure_validation_errors_fail_closed(
    payload: dict,
    rollback_plan: str,
    expected_reason_fragment: str,
) -> None:
    pack = create_infrastructure_domain_pack()
    proposal = pack.proposal_adapter.build_proposal(
        target_resource="infra/target",
        summary="Invalid infra proposal",
        payload=payload,
        rollback_plan=rollback_plan,
    )
    decision = evaluate_domain_proposal(
        pack,
        proposal,
        run_id="run-infra-invalid",
        builder_actor="infra-builder",
        verifier_actor="infra-verifier",
        human_approvers=["g1", "g2"],
        explicit_authorization_ref="CAB-OVERRIDE",
    )
    assert decision.can_release is False
    assert decision.outcome == ReleaseOutcome.DENIED_POLICY
    assert any(expected_reason_fragment in reason for reason in decision.reasons)


def test_infrastructure_public_edge_override_and_plan_failure_and_sod() -> None:
    pack = create_infrastructure_domain_pack()

    # public_edge_approved=True allows 0.0.0.0/0 when explicitly approved
    edge_prop = pack.proposal_adapter.build_proposal(
        target_resource="aws_security_group.public_alb",
        summary="Allow HTTPS on approved public edge ALB",
        payload={
            "environment": "production",
            "cidr_blocks": ["0.0.0.0/0"],
            "public_edge_approved": True,
        },
        rollback_plan="terraform apply -var-file=prev.tfvars",
    )
    edge_dec = evaluate_domain_proposal(
        pack,
        edge_prop,
        run_id="run-infra-edge",
        builder_actor="infra-builder",
        verifier_actor="infra-verifier",
        human_approvers=["netsec-lead"],
    )
    assert edge_dec.can_release is True
    assert edge_dec.outcome == ReleaseOutcome.APPROVED_WITH_HUMAN

    # plan_checks_passed=False fails closed
    failed_plan_prop = pack.proposal_adapter.build_proposal(
        target_resource="aws_vpc.dev",
        summary="Broken plan",
        payload={"environment": "dev", "plan_checks_passed": False},
        rollback_plan="terraform apply -var-file=prev.tfvars",
    )
    failed_dec = evaluate_domain_proposal(
        pack,
        failed_plan_prop,
        run_id="run-infra-plan-fail",
        builder_actor="infra-builder",
        verifier_actor="infra-verifier",
    )
    assert failed_dec.can_release is False
    assert failed_dec.outcome == ReleaseOutcome.DENIED_POLICY

    # Actor separation raises PermissionError
    with pytest.raises(PermissionError, match="distinct from builder_actor"):
        evaluate_domain_proposal(
            pack,
            edge_prop,
            run_id="run-infra-sod",
            builder_actor="infra-actor",
            verifier_actor="infra-actor",
        )


def test_operations_risk_tiers_and_autonomy_lifecycle() -> None:
    pack = create_operations_domain_pack()
    adapter = pack.proposal_adapter
    policy = pack.policy_pack
    assert isinstance(adapter, OperationsProposalAdapter)

    # LOW
    low_prop = adapter.build_proposal(
        target_resource="svc/dev-worker",
        summary="Clear dev worker dead-letter queue",
        payload={"environment": "dev", "action_type": "drain_dlq", "blast_radius_pct": 5},
        rollback_plan="Re-enqueue archived messages from backup",
    )
    assert policy.default_risk_tier(low_prop) == RiskTier.LOW
    assert policy.required_assurance(low_prop) == AssuranceLevel.A1_BASIC
    low_dec = evaluate_domain_proposal(
        pack,
        low_prop,
        run_id="run-ops-low",
        builder_actor="ops-agent",
        verifier_actor="ops-verifier",
    )
    assert low_dec.can_release is True
    assert low_dec.outcome == ReleaseOutcome.AUTONOMOUS_RELEASE

    # MEDIUM (staging or traffic_shift / restart_fleet)
    med_prop = adapter.build_proposal(
        target_resource="svc/checkout-canary",
        summary="Shift 10% staging traffic to canary",
        payload={"environment": "staging", "action_type": "traffic_shift", "blast_radius_pct": 10},
        rollback_plan="Shift 100% traffic back to baseline revision",
    )
    assert policy.default_risk_tier(med_prop) == RiskTier.MEDIUM
    assert policy.required_assurance(med_prop) == AssuranceLevel.A2_INDEPENDENT
    med_dec = evaluate_domain_proposal(
        pack,
        med_prop,
        run_id="run-ops-med",
        builder_actor="ops-agent",
        verifier_actor="ops-verifier",
    )
    assert med_dec.can_release is True
    assert med_dec.outcome == ReleaseOutcome.AUTONOMOUS_RELEASE

    # HIGH (production with slo_target + health_check_probe, or blast_radius_pct > 10)
    high_prop = adapter.build_proposal(
        target_resource="svc/payments-api",
        summary="Restart degraded pod slice in prod",
        payload={
            "environment": "prod",
            "action_type": "restart_fleet",
            "blast_radius_pct": 25,
            "slo_target": "99.95% availability",
            "health_check_probe": "http://127.0.0.1:8080/healthz",
        },
        rollback_plan="Abort rolling restart and restore previous ReplicaSet",
    )
    assert policy.default_risk_tier(high_prop) == RiskTier.HIGH
    assert policy.required_assurance(high_prop) == AssuranceLevel.A3_ATTESTED

    high_blocked = evaluate_domain_proposal(
        pack,
        high_prop,
        run_id="run-ops-high-1",
        builder_actor="ops-agent",
        verifier_actor="ops-verifier",
    )
    assert high_blocked.can_release is False
    assert high_blocked.outcome == ReleaseOutcome.HUMAN_APPROVAL_REQUIRED

    high_approved = evaluate_domain_proposal(
        pack,
        high_prop,
        run_id="run-ops-high-2",
        builder_actor="ops-agent",
        verifier_actor="ops-verifier",
        human_approvers=["oncall-sre"],
    )
    assert high_approved.can_release is True
    assert high_approved.outcome == ReleaseOutcome.APPROVED_WITH_HUMAN

    with pytest.raises(PermissionError):
        adapter.apply_proposal(high_prop, approved_decision_id="")

    receipt = adapter.apply_proposal(high_prop, approved_decision_id=high_approved.decision_id)
    assert receipt["status"] == "executed"
    assert receipt["domain"] == "operations"
    assert receipt["proposal_id"] == high_prop.proposal_id
    assert receipt["approved_decision_id"] == high_approved.decision_id
    assert receipt["payload_digest"] == high_prop.payload_digest
    assert adapter.executed_runbooks == [receipt]

    # CRITICAL (blast_radius_pct > 50 or region_failover/data_purge/break_glass)
    for crit_payload in (
        {
            "environment": "production",
            "action_type": "traffic_shift",
            "blast_radius_pct": 75,
            "slo_target": "99.9%",
            "health_check_probe": "http://127.0.0.1:8080/ready",
        },
        {
            "environment": "staging",
            "action_type": "region_failover",
            "blast_radius_pct": 10,
        },
        {
            "environment": "dev",
            "action_type": "data_purge",
        },
        {
            "environment": "dev",
            "action_type": "break_glass",
        },
    ):
        crit_prop = adapter.build_proposal(
            target_resource="cluster/primary",
            summary="Critical operational action",
            payload=crit_payload,
            rollback_plan="Execute reverse failover runbook",
        )
        assert policy.default_risk_tier(crit_prop) == RiskTier.CRITICAL
        assert policy.required_assurance(crit_prop) == AssuranceLevel.A4_RESTRICTED

        crit_blocked = evaluate_domain_proposal(
            pack,
            crit_prop,
            run_id="run-ops-crit-blocked",
            builder_actor="ops-agent",
            verifier_actor="ops-verifier",
            human_approvers=["incident-commander"],
            explicit_authorization_ref="INC-2026-77",
        )
        assert crit_blocked.can_release is False
        assert crit_blocked.outcome == ReleaseOutcome.RESTRICTED_AUTHORIZATION_REQUIRED

        crit_approved = evaluate_domain_proposal(
            pack,
            crit_prop,
            run_id="run-ops-crit-ok",
            builder_actor="ops-agent",
            verifier_actor="ops-verifier",
            human_approvers=["incident-commander", "vp-engineering"],
            explicit_authorization_ref="INC-2026-77",
        )
        assert crit_approved.can_release is True
        assert crit_approved.outcome == ReleaseOutcome.APPROVED_RESTRICTED


@pytest.mark.parametrize(
    ("payload", "rollback_plan", "expected_reason_fragment"),
    [
        ({"environment": "dev", "blast_radius_pct": 5}, "", "rollback_plan"),
        ({"environment": "dev", "blast_radius_pct": -1}, "undo", "blast_radius_pct"),
        ({"environment": "dev", "blast_radius_pct": 101}, "undo", "blast_radius_pct"),
        ({"environment": "dev", "blast_radius_pct": "fifty"}, "undo", "blast_radius_pct"),
        (
            {"environment": "production", "health_check_probe": "http://127.0.0.1:8080/healthz"},
            "undo",
            "slo_target",
        ),
        (
            {"environment": "prod", "slo_target": "99.9%"},
            "undo",
            "health_check_probe",
        ),
    ],
)
def test_operations_validation_errors_fail_closed(
    payload: dict,
    rollback_plan: str,
    expected_reason_fragment: str,
) -> None:
    pack = create_operations_domain_pack()
    proposal = pack.proposal_adapter.build_proposal(
        target_resource="svc/api",
        summary="Invalid operations proposal",
        payload=payload,
        rollback_plan=rollback_plan,
    )
    decision = evaluate_domain_proposal(
        pack,
        proposal,
        run_id="run-ops-invalid",
        builder_actor="ops-agent",
        verifier_actor="ops-verifier",
        human_approvers=["g1", "g2"],
        explicit_authorization_ref="INC-1",
    )
    assert decision.can_release is False
    assert decision.outcome == ReleaseOutcome.DENIED_POLICY
    assert any(expected_reason_fragment in reason for reason in decision.reasons)


def test_operations_preflight_failure_and_actor_separation() -> None:
    pack = create_operations_domain_pack()
    prop = pack.proposal_adapter.build_proposal(
        target_resource="svc/search",
        summary="Restart search fleet",
        payload={
            "environment": "staging",
            "action_type": "restart_fleet",
            "preflight_healthy": False,
        },
        rollback_plan="Restore previous replica set",
    )
    denied = evaluate_domain_proposal(
        pack,
        prop,
        run_id="run-ops-preflight-fail",
        builder_actor="ops-agent",
        verifier_actor="ops-verifier",
    )
    assert denied.can_release is False
    assert denied.outcome == ReleaseOutcome.DENIED_POLICY

    with pytest.raises(PermissionError, match="distinct from builder_actor"):
        evaluate_domain_proposal(
            pack,
            prop,
            run_id="run-ops-sod",
            builder_actor="ops-agent",
            verifier_actor="ops-agent",
        )
