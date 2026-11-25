"""Tests for Scope B Business and Compliance Domain Packs."""
from __future__ import annotations

import pytest

from vad.adapters.domain_pack import (
    ArtifactProposalAdapter,
    DomainKind,
    DomainPackRegistry,
    DomainPolicyPack,
    ProofExecutorAdapter,
    evaluate_domain_proposal,
)
from vad.adapters.packs.business import (
    BusinessPolicyPack,
    BusinessProofExecutor,
    BusinessProposalAdapter,
    create_business_domain_pack,
)
from vad.adapters.packs.compliance import (
    CompliancePolicyPack,
    ComplianceProofExecutor,
    ComplianceProposalAdapter,
    create_compliance_domain_pack,
)
from vad.contracts.models import AssuranceLevel, RiskTier
from vad.release.decision import ReleaseOutcome


def test_factories_satisfy_protocols_and_register_cleanly() -> None:
    business_pack = create_business_domain_pack()
    compliance_pack = create_compliance_domain_pack()

    assert business_pack.domain == DomainKind.BUSINESS
    assert compliance_pack.domain == DomainKind.COMPLIANCE

    assert isinstance(business_pack.proposal_adapter, BusinessProposalAdapter)
    assert isinstance(business_pack.proof_executor, BusinessProofExecutor)
    assert isinstance(business_pack.policy_pack, BusinessPolicyPack)

    assert isinstance(compliance_pack.proposal_adapter, ComplianceProposalAdapter)
    assert isinstance(compliance_pack.proof_executor, ComplianceProofExecutor)
    assert isinstance(compliance_pack.policy_pack, CompliancePolicyPack)

    for pack in (business_pack, compliance_pack):
        assert isinstance(pack.proposal_adapter, ArtifactProposalAdapter)
        assert isinstance(pack.proof_executor, ProofExecutorAdapter)
        assert isinstance(pack.policy_pack, DomainPolicyPack)

    registry = DomainPackRegistry()
    registry.register(business_pack)
    registry.register(compliance_pack)
    assert registry.list_domains() == [DomainKind.BUSINESS, DomainKind.COMPLIANCE]


def test_business_risk_tiers_and_assurance_mapping() -> None:
    pack = create_business_domain_pack()

    low_prop = pack.proposal_adapter.build_proposal(
        target_resource="pricing/rule/smb-promo",
        summary="Apply 10% seasonal discount for SMB tier",
        payload={"financial_impact_usd": 250.0, "discount_pct": 10},
        rollback_plan="Disable promo rule smb-promo",
    )
    med_prop = pack.proposal_adapter.build_proposal(
        target_resource="billing/credit/acct-104",
        summary="Issue SLA service credit",
        payload={"financial_impact_usd": 1200.0},
        rollback_plan="Void unapplied billing credit memo",
    )
    high_impact_prop = pack.proposal_adapter.build_proposal(
        target_resource="entitlements/tier/growth-plus",
        summary="Adjust annual plan renewal pricing",
        payload={"financial_impact_usd": 5000.0, "policy_reference": "FIN-POL-2026-01"},
        rollback_plan="Restore previous pricing catalog version",
    )
    high_segment_prop = pack.proposal_adapter.build_proposal(
        target_resource="entitlements/enterprise/acme",
        summary="Enable enterprise seat burst entitlement",
        payload={"financial_impact_usd": 100.0, "customer_segment": "enterprise"},
        rollback_plan="Revert enterprise entitlement snapshot",
    )
    high_segment_camel_prop = pack.proposal_adapter.build_proposal(
        target_resource="entitlements/enterprise/globex",
        summary="Update enterprise support tier",
        payload={"financial_impact_usd": 0.0, "customer_Segment": "enterprise"},
        rollback_plan="Revert Globex entitlement tier",
    )
    crit_impact_prop = pack.proposal_adapter.build_proposal(
        target_resource="contracts/transition/enterprise-renewal",
        summary="Approve automated enterprise contract amendment",
        payload={"financial_impact_usd": 50000.0, "policy_reference": "REV-2026-08"},
        rollback_plan="Cancel pending contract transition",
    )
    crit_ledger_prop = pack.proposal_adapter.build_proposal(
        target_resource="billing/tax-mapping/eu-vat",
        summary="Update EU VAT ledger posting rule",
        payload={
            "financial_impact_usd": 200.0,
            "affects_ledger_or_tax": True,
            "policy_reference": "TAX-2026-04",
        },
        rollback_plan="Restore prior tax posting mapping",
    )

    assert pack.policy_pack.default_risk_tier(low_prop) == RiskTier.LOW
    assert pack.policy_pack.required_assurance(low_prop) == AssuranceLevel.A1_BASIC

    assert pack.policy_pack.default_risk_tier(med_prop) == RiskTier.MEDIUM
    assert pack.policy_pack.required_assurance(med_prop) == AssuranceLevel.A2_INDEPENDENT

    for high_prop in (high_impact_prop, high_segment_prop, high_segment_camel_prop):
        assert pack.policy_pack.default_risk_tier(high_prop) == RiskTier.HIGH
        assert pack.policy_pack.required_assurance(high_prop) == AssuranceLevel.A3_ATTESTED

    for crit_prop in (crit_impact_prop, crit_ledger_prop):
        assert pack.policy_pack.default_risk_tier(crit_prop) == RiskTier.CRITICAL
        assert pack.policy_pack.required_assurance(crit_prop) == AssuranceLevel.A4_RESTRICTED


def test_business_validation_failures_and_simulation_gate() -> None:
    pack = create_business_domain_pack()

    invalid = pack.proposal_adapter.build_proposal(
        target_resource="pricing/rule/bad",
        summary="Invalid pricing update",
        payload={
            "financial_impact_usd": -100.0,
            "discount_pct": 120.0,
            "affects_ledger_or_tax": True,
        },
        rollback_plan="   ",
    )
    errors = pack.policy_pack.validate_proposal(invalid)
    assert len(errors) == 4

    denied = evaluate_domain_proposal(
        pack,
        invalid,
        run_id="run-biz-invalid-1",
        builder_actor="revops-agent",
        verifier_actor="fin-sim-verifier",
        human_approvers=["cfo-delegate", "controller"],
        explicit_authorization_ref="FIN-CAB-1",
    )
    assert denied.can_release is False
    assert denied.outcome == ReleaseOutcome.DENIED_POLICY
    assert any("rollback_plan" in r for r in denied.reasons)
    assert any("financial_impact_usd" in r for r in denied.reasons)
    assert any("discount_pct" in r for r in denied.reasons)
    assert any("policy_reference" in r for r in denied.reasons)

    missing_policy_ref = pack.proposal_adapter.build_proposal(
        target_resource="billing/credit/large",
        summary="High-value credit without policy reference",
        payload={"financial_impact_usd": 6500.0, "discount_pct": -5},
        rollback_plan="Cancel credit adjustment",
    )
    ref_errors = pack.policy_pack.validate_proposal(missing_policy_ref)
    assert any("discount_pct" in e for e in ref_errors)
    assert any("policy_reference" in e for e in ref_errors)

    # Boundary discount_pct 0 and 100 are valid
    boundary_ok = pack.proposal_adapter.build_proposal(
        target_resource="pricing/rule/full-comp",
        summary="100% comp rule for internal sandbox",
        payload={"financial_impact_usd": 100.0, "discount_pct": 100},
        rollback_plan="Disable rule",
    )
    assert pack.policy_pack.validate_proposal(boundary_ok) == []

    # Failed invariant simulation blocks release
    failed_sim = pack.proposal_adapter.build_proposal(
        target_resource="pricing/rule/smb-promo",
        summary="Failed invariant simulation",
        payload={"financial_impact_usd": 200.0, "simulation_passed": False},
        rollback_plan="Disable rule",
    )
    sim_denied = evaluate_domain_proposal(
        pack,
        failed_sim,
        run_id="run-biz-sim-fail",
        builder_actor="revops-agent",
        verifier_actor="fin-sim-verifier",
    )
    assert sim_denied.can_release is False
    assert sim_denied.outcome == ReleaseOutcome.DENIED_POLICY


def test_business_governance_gates_and_apply_receipt() -> None:
    pack = create_business_domain_pack()
    assert isinstance(pack.proposal_adapter, BusinessProposalAdapter)

    # LOW and MEDIUM can release autonomously with distinct verifier
    low_prop = pack.proposal_adapter.build_proposal(
        target_resource="pricing/rule/trial-ext",
        summary="Extend trial discount",
        payload={"financial_impact_usd": 150.0},
        rollback_plan="Revert trial rule",
    )
    low_decision = evaluate_domain_proposal(
        pack,
        low_prop,
        run_id="run-biz-low",
        builder_actor="revops-agent",
        verifier_actor="fin-verifier",
    )
    assert low_decision.can_release is True
    assert low_decision.outcome == ReleaseOutcome.AUTONOMOUS_RELEASE

    # Actor separation enforced
    with pytest.raises(PermissionError, match="distinct from builder_actor"):
        evaluate_domain_proposal(
            pack,
            low_prop,
            run_id="run-biz-sod",
            builder_actor="revops-agent",
            verifier_actor="revops-agent",
        )

    # HIGH requires human approval and blocks builder self-approval
    high_prop = pack.proposal_adapter.build_proposal(
        target_resource="billing/credit/ent-credit",
        summary="Enterprise billing credit",
        payload={
            "financial_impact_usd": 8000.0,
            "customer_segment": "enterprise",
            "policy_reference": "FIN-POL-2026-02",
        },
        rollback_plan="Reverse enterprise credit memo",
    )
    blocked_high = evaluate_domain_proposal(
        pack,
        high_prop,
        run_id="run-biz-high-1",
        builder_actor="revops-agent",
        verifier_actor="fin-verifier",
    )
    assert blocked_high.can_release is False
    assert blocked_high.outcome == ReleaseOutcome.HUMAN_APPROVAL_REQUIRED

    self_approved = evaluate_domain_proposal(
        pack,
        high_prop,
        run_id="run-biz-high-self",
        builder_actor="revops-agent",
        verifier_actor="fin-verifier",
        human_approvers=["revops-agent"],
    )
    assert self_approved.can_release is False
    assert self_approved.outcome == ReleaseOutcome.DENIED_POLICY

    approved_high = evaluate_domain_proposal(
        pack,
        high_prop,
        run_id="run-biz-high-2",
        builder_actor="revops-agent",
        verifier_actor="fin-verifier",
        human_approvers=["finance-director"],
    )
    assert approved_high.can_release is True
    assert approved_high.outcome == ReleaseOutcome.APPROVED_WITH_HUMAN

    # CRITICAL requires dual sign-off + explicit authorization ref
    crit_prop = pack.proposal_adapter.build_proposal(
        target_resource="billing/ledger/q4-accrual",
        summary="Adjust Q4 revenue recognition accrual",
        payload={
            "financial_impact_usd": 75000.0,
            "affects_ledger_or_tax": True,
            "policy_reference": "SOX-FIN-09",
        },
        rollback_plan="Post reversing accrual journal entry",
    )
    blocked_single = evaluate_domain_proposal(
        pack,
        crit_prop,
        run_id="run-biz-crit-1",
        builder_actor="revops-agent",
        verifier_actor="fin-verifier",
        human_approvers=["vp-finance"],
        explicit_authorization_ref="CAB-FIN-2026-11",
    )
    assert blocked_single.can_release is False
    assert blocked_single.outcome == ReleaseOutcome.RESTRICTED_AUTHORIZATION_REQUIRED

    blocked_no_auth = evaluate_domain_proposal(
        pack,
        crit_prop,
        run_id="run-biz-crit-2",
        builder_actor="revops-agent",
        verifier_actor="fin-verifier",
        human_approvers=["vp-finance", "corporate-controller"],
    )
    assert blocked_no_auth.can_release is False
    assert blocked_no_auth.outcome == ReleaseOutcome.RESTRICTED_AUTHORIZATION_REQUIRED

    approved_crit = evaluate_domain_proposal(
        pack,
        crit_prop,
        run_id="run-biz-crit-3",
        builder_actor="revops-agent",
        verifier_actor="fin-verifier",
        human_approvers=["vp-finance", "corporate-controller"],
        explicit_authorization_ref="CAB-FIN-2026-11",
        obligation_ids=["ledger-balance-invariant", "tax-boundary-sim"],
    )
    assert approved_crit.can_release is True
    assert approved_crit.outcome == ReleaseOutcome.APPROVED_RESTRICTED
    assert approved_crit.recorded_approvers == ["vp-finance", "corporate-controller"]
    assert approved_crit.explicit_authorization_ref == "CAB-FIN-2026-11"

    # apply_proposal validates approved_decision_id and tracks applied_workflows
    with pytest.raises(PermissionError, match="non-empty"):
        pack.proposal_adapter.apply_proposal(crit_prop, approved_decision_id="   ")

    receipt = pack.proposal_adapter.apply_proposal(
        crit_prop, approved_decision_id=approved_crit.decision_id
    )
    assert receipt == {
        "status": "applied",
        "domain": "business",
        "proposal_id": crit_prop.proposal_id,
        "target_resource": crit_prop.target_resource,
        "approved_decision_id": approved_crit.decision_id,
        "payload_digest": crit_prop.payload_digest,
    }
    assert pack.proposal_adapter.applied_workflows == [receipt]


def test_compliance_risk_tiers_and_validation_rules() -> None:
    pack = create_compliance_domain_pack()

    internal_prop = pack.proposal_adapter.build_proposal(
        target_resource="retention/internal-logs",
        summary="Adjust internal engineering debug log retention",
        payload={"internal_only": True, "audit_ticket": "AUD-2026-201"},
        rollback_plan="Restore 30-day retention policy",
    )
    assert pack.policy_pack.default_risk_tier(internal_prop) == RiskTier.MEDIUM
    assert pack.policy_pack.required_assurance(internal_prop) == AssuranceLevel.A2_INDEPENDENT

    for framework in ("SOC2", "ISO27001", "HIPAA", "PCI-DSS"):
        fw_prop = pack.proposal_adapter.build_proposal(
            target_resource=f"controls/{framework.lower()}/access-review",
            summary=f"Quarterly {framework} access review sign-off",
            payload={"framework": framework, "audit_ticket": "AUD-2026-202"},
            rollback_plan="Re-open access review campaign",
        )
        assert pack.policy_pack.default_risk_tier(fw_prop) == RiskTier.HIGH
        assert pack.policy_pack.required_assurance(fw_prop) == AssuranceLevel.A3_ATTESTED

    unspecified_fw_prop = pack.proposal_adapter.build_proposal(
        target_resource="controls/generic/policy",
        summary="Update general compliance policy",
        payload={"audit_ticket": "AUD-2026-203"},
        rollback_plan="Revert policy revision",
    )
    assert pack.policy_pack.default_risk_tier(unspecified_fw_prop) == RiskTier.HIGH
    assert pack.policy_pack.required_assurance(unspecified_fw_prop) == AssuranceLevel.A3_ATTESTED

    for family in ("SOC2-CC6", "ISO27001-A.9", "GDPR-ERASURE"):
        crit_family = pack.proposal_adapter.build_proposal(
            target_resource=f"controls/{family}",
            summary=f"Attest {family} control update",
            payload={"control_family": family, "audit_ticket": "AUD-2026-204"},
            rollback_plan="Restore prior control baseline",
        )
        assert pack.policy_pack.default_risk_tier(crit_family) == RiskTier.CRITICAL
        assert pack.policy_pack.required_assurance(crit_family) == AssuranceLevel.A4_RESTRICTED

    # Validation failures: missing rollback_plan, missing audit_ticket, exception missing fields
    invalid_exception = pack.proposal_adapter.build_proposal(
        target_resource="exceptions/prod-ssh",
        summary="Temporary break-glass policy exception",
        payload={"exception_request": True},
        rollback_plan="",
    )
    assert pack.policy_pack.default_risk_tier(invalid_exception) == RiskTier.CRITICAL
    errors = pack.policy_pack.validate_proposal(invalid_exception)
    assert any("rollback_plan" in e for e in errors)
    assert any("audit_ticket" in e for e in errors)
    assert any("expires_at" in e for e in errors)
    assert any("compensating_control" in e for e in errors)

    denied = evaluate_domain_proposal(
        pack,
        invalid_exception,
        run_id="run-comp-invalid",
        builder_actor="compliance-agent",
        verifier_actor="audit-verifier",
        human_approvers=["ciso", "dpo"],
        explicit_authorization_ref="SEC-CAB-99",
    )
    assert denied.can_release is False
    assert denied.outcome == ReleaseOutcome.DENIED_POLICY

    # Control check failure blocks even when validation passes
    failed_control = pack.proposal_adapter.build_proposal(
        target_resource="controls/soc2/backup-check",
        summary="Backup control attestation",
        payload={
            "framework": "SOC2",
            "audit_ticket": "AUD-2026-205",
            "control_check_passed": False,
        },
        rollback_plan="Mark control attestation pending",
    )
    control_denied = evaluate_domain_proposal(
        pack,
        failed_control,
        run_id="run-comp-ctrl-fail",
        builder_actor="compliance-agent",
        verifier_actor="audit-verifier",
        human_approvers=["security-lead"],
    )
    assert control_denied.can_release is False
    assert control_denied.outcome == ReleaseOutcome.DENIED_POLICY


def test_compliance_governance_gates_and_attestation_receipt() -> None:
    pack = create_compliance_domain_pack()
    assert isinstance(pack.proposal_adapter, ComplianceProposalAdapter)

    # MEDIUM internal-only releases autonomously with independent verifier
    med_prop = pack.proposal_adapter.build_proposal(
        target_resource="retention/dev-telemetry",
        summary="Update internal telemetry retention",
        payload={"internal_only": True, "audit_ticket": "AUD-2026-301"},
        rollback_plan="Restore prior retention config",
    )
    med_decision = evaluate_domain_proposal(
        pack,
        med_prop,
        run_id="run-comp-med",
        builder_actor="compliance-agent",
        verifier_actor="audit-verifier",
    )
    assert med_decision.can_release is True
    assert med_decision.outcome == ReleaseOutcome.AUTONOMOUS_RELEASE

    # Actor separation enforced
    with pytest.raises(PermissionError, match="distinct from builder_actor"):
        evaluate_domain_proposal(
            pack,
            med_prop,
            run_id="run-comp-sod",
            builder_actor="compliance-agent",
            verifier_actor="  compliance-agent ",
        )

    # HIGH requires human approval
    high_prop = pack.proposal_adapter.build_proposal(
        target_resource="controls/iso27001/access-review",
        summary="Quarterly ISO27001 access review sign-off",
        payload={"framework": "ISO27001", "audit_ticket": "AUD-2026-302"},
        rollback_plan="Re-open access review",
    )
    blocked_high = evaluate_domain_proposal(
        pack,
        high_prop,
        run_id="run-comp-high-1",
        builder_actor="compliance-agent",
        verifier_actor="audit-verifier",
    )
    assert blocked_high.can_release is False
    assert blocked_high.outcome == ReleaseOutcome.HUMAN_APPROVAL_REQUIRED

    approved_high = evaluate_domain_proposal(
        pack,
        high_prop,
        run_id="run-comp-high-2",
        builder_actor="compliance-agent",
        verifier_actor="audit-verifier",
        human_approvers=["compliance-officer"],
    )
    assert approved_high.can_release is True
    assert approved_high.outcome == ReleaseOutcome.APPROVED_WITH_HUMAN

    # CRITICAL bounded policy exception requires dual sign-off + explicit authorization
    crit_exception = pack.proposal_adapter.build_proposal(
        target_resource="policy/exception/legacy-tls",
        summary="Bounded 14-day TLS 1.1 exception for partner migration",
        payload={
            "exception_request": True,
            "audit_ticket": "AUD-2026-303",
            "expires_at": "2026-12-08T00:00:00Z",
            "compensating_control": "IP allowlist + mTLS gateway proxy",
        },
        rollback_plan="Revoke partner legacy TLS exception immediately",
    )
    blocked_crit = evaluate_domain_proposal(
        pack,
        crit_exception,
        run_id="run-comp-crit-1",
        builder_actor="compliance-agent",
        verifier_actor="audit-verifier",
        human_approvers=["ciso"],
        explicit_authorization_ref="EXC-CAB-2026-07",
    )
    assert blocked_crit.can_release is False
    assert blocked_crit.outcome == ReleaseOutcome.RESTRICTED_AUTHORIZATION_REQUIRED

    approved_crit = evaluate_domain_proposal(
        pack,
        crit_exception,
        run_id="run-comp-crit-2",
        builder_actor="compliance-agent",
        verifier_actor="audit-verifier",
        human_approvers=["ciso", "risk-committee-chair"],
        explicit_authorization_ref="EXC-CAB-2026-07",
        obligation_ids=["audit-ticket-evidence", "compensating-control-check"],
    )
    assert approved_crit.can_release is True
    assert approved_crit.outcome == ReleaseOutcome.APPROVED_RESTRICTED
    assert approved_crit.recorded_approvers == ["ciso", "risk-committee-chair"]
    assert approved_crit.explicit_authorization_ref == "EXC-CAB-2026-07"

    with pytest.raises(PermissionError, match="non-empty"):
        pack.proposal_adapter.apply_proposal(crit_exception, approved_decision_id="")

    receipt = pack.proposal_adapter.apply_proposal(
        crit_exception, approved_decision_id=approved_crit.decision_id
    )
    assert receipt == {
        "status": "attested",
        "domain": "compliance",
        "proposal_id": crit_exception.proposal_id,
        "target_resource": crit_exception.target_resource,
        "approved_decision_id": approved_crit.decision_id,
        "payload_digest": crit_exception.payload_digest,
    }
    assert pack.proposal_adapter.recorded_attestations == [receipt]
