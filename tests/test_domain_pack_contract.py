"""Contract tests for Scope B Domain Pack adapter seam and VAD core reuse."""
from __future__ import annotations

import hashlib
import json
from typing import Any, Optional

import pytest
from pydantic import ValidationError

from vad.adapters.domain_pack import (
    ArtifactProposalAdapter,
    DomainArtifactProposal,
    DomainKind,
    DomainPack,
    DomainPackRegistry,
    DomainPolicyPack,
    DomainProofOutcome,
    ProofExecutorAdapter,
    evaluate_domain_proposal,
)
from vad.contracts.models import AssuranceLevel, RiskTier
from vad.release.decision import ReleaseDecision, ReleaseOutcome


class MockInfrastructureProposalAdapter:
    domain = DomainKind.INFRASTRUCTURE

    def __init__(self) -> None:
        self.applied: list[dict[str, Any]] = []

    def build_proposal(
        self,
        target_resource: str,
        summary: str,
        payload: dict[str, Any],
        rollback_plan: str = "",
        proposal_id: Optional[str] = None,
    ) -> DomainArtifactProposal:
        kwargs: dict[str, Any] = {
            "domain": self.domain,
            "target_resource": target_resource,
            "summary": summary,
            "payload": payload,
            "rollback_plan": rollback_plan,
        }
        if proposal_id is not None:
            kwargs["proposal_id"] = proposal_id
        return DomainArtifactProposal(**kwargs)

    def apply_proposal(
        self,
        proposal: DomainArtifactProposal,
        *,
        approved_decision_id: str,
    ) -> dict[str, Any]:
        record = {
            "status": "applied",
            "proposal_id": proposal.proposal_id,
            "target_resource": proposal.target_resource,
            "approved_decision_id": approved_decision_id,
            "payload_digest": proposal.payload_digest,
        }
        self.applied.append(record)
        return record


class MockInfrastructureProofExecutor:
    domain = DomainKind.INFRASTRUCTURE

    def __init__(self, *, isolated: bool = True, pass_checks: bool = True) -> None:
        self.isolated = isolated
        self.pass_checks = pass_checks

    def execute_obligation(
        self,
        proposal: DomainArtifactProposal,
        obligation_id: str,
        *,
        verifier_actor: str,
    ) -> DomainProofOutcome:
        return DomainProofOutcome(
            obligation_id=obligation_id,
            passed=self.pass_checks,
            summary=f"Terraform plan dry-run for {proposal.target_resource}",
            verifier_actor=verifier_actor,
            isolated=self.isolated,
        )


class MockInfrastructurePolicyPack:
    domain = DomainKind.INFRASTRUCTURE

    def default_risk_tier(self, proposal: DomainArtifactProposal) -> RiskTier:
        env = str(proposal.payload.get("environment", "staging")).lower()
        if env == "production":
            return RiskTier.HIGH
        return RiskTier.LOW

    def required_assurance(self, proposal: DomainArtifactProposal) -> AssuranceLevel:
        if self.default_risk_tier(proposal) == RiskTier.HIGH:
            return AssuranceLevel.A3_ATTESTED
        return AssuranceLevel.A1_BASIC

    def validate_proposal(self, proposal: DomainArtifactProposal) -> list[str]:
        errors: list[str] = []
        if not proposal.rollback_plan.strip():
            errors.append("Infrastructure proposal must declare a non-empty rollback_plan")
        if "0.0.0.0/0" in proposal.payload.get("cidr_blocks", []):
            errors.append("Wildcard ingress 0.0.0.0/0 is forbidden")
        return errors


class MockComplianceProposalAdapter:
    domain = DomainKind.COMPLIANCE

    def build_proposal(
        self,
        target_resource: str,
        summary: str,
        payload: dict[str, Any],
        rollback_plan: str = "",
        proposal_id: Optional[str] = None,
    ) -> DomainArtifactProposal:
        kwargs: dict[str, Any] = {
            "domain": self.domain,
            "target_resource": target_resource,
            "summary": summary,
            "payload": payload,
            "rollback_plan": rollback_plan,
        }
        if proposal_id is not None:
            kwargs["proposal_id"] = proposal_id
        return DomainArtifactProposal(**kwargs)

    def apply_proposal(
        self,
        proposal: DomainArtifactProposal,
        *,
        approved_decision_id: str,
    ) -> dict[str, Any]:
        return {
            "status": "attested",
            "proposal_id": proposal.proposal_id,
            "approved_decision_id": approved_decision_id,
        }


class MockComplianceProofExecutor:
    domain = DomainKind.COMPLIANCE

    def execute_obligation(
        self,
        proposal: DomainArtifactProposal,
        obligation_id: str,
        *,
        verifier_actor: str,
    ) -> DomainProofOutcome:
        has_ticket = bool(proposal.payload.get("audit_ticket"))
        return DomainProofOutcome(
            obligation_id=obligation_id,
            passed=has_ticket,
            summary=f"Compliance control verification ({obligation_id})",
            verifier_actor=verifier_actor,
            isolated=True,
        )


class MockCompliancePolicyPack:
    domain = DomainKind.COMPLIANCE

    def default_risk_tier(self, proposal: DomainArtifactProposal) -> RiskTier:
        if proposal.payload.get("control_family") == "SOC2-CC6":
            return RiskTier.CRITICAL
        return RiskTier.HIGH

    def required_assurance(self, proposal: DomainArtifactProposal) -> AssuranceLevel:
        if self.default_risk_tier(proposal) == RiskTier.CRITICAL:
            return AssuranceLevel.A4_RESTRICTED
        return AssuranceLevel.A3_ATTESTED

    def validate_proposal(self, proposal: DomainArtifactProposal) -> list[str]:
        errors: list[str] = []
        if not proposal.rollback_plan.strip():
            errors.append("Compliance proposal must specify rollback_plan")
        if not proposal.payload.get("audit_ticket"):
            errors.append("Compliance proposal requires an audit_ticket reference")
        return errors


def _build_registry() -> DomainPackRegistry:
    registry = DomainPackRegistry()
    registry.register(
        DomainPack(
            domain=DomainKind.INFRASTRUCTURE,
            proposal_adapter=MockInfrastructureProposalAdapter(),
            proof_executor=MockInfrastructureProofExecutor(),
            policy_pack=MockInfrastructurePolicyPack(),
        )
    )
    registry.register(
        DomainPack(
            domain=DomainKind.COMPLIANCE,
            proposal_adapter=MockComplianceProposalAdapter(),
            proof_executor=MockComplianceProofExecutor(),
            policy_pack=MockCompliancePolicyPack(),
        )
    )
    return registry


def test_runtime_protocol_checks_and_registry() -> None:
    infra_proposal_adapter = MockInfrastructureProposalAdapter()
    infra_proof_executor = MockInfrastructureProofExecutor()
    infra_policy_pack = MockInfrastructurePolicyPack()

    comp_proposal_adapter = MockComplianceProposalAdapter()
    comp_proof_executor = MockComplianceProofExecutor()
    comp_policy_pack = MockCompliancePolicyPack()

    assert isinstance(infra_proposal_adapter, ArtifactProposalAdapter)
    assert isinstance(infra_proof_executor, ProofExecutorAdapter)
    assert isinstance(infra_policy_pack, DomainPolicyPack)

    assert isinstance(comp_proposal_adapter, ArtifactProposalAdapter)
    assert isinstance(comp_proof_executor, ProofExecutorAdapter)
    assert isinstance(comp_policy_pack, DomainPolicyPack)

    assert not isinstance(object(), ArtifactProposalAdapter)
    assert not isinstance(object(), ProofExecutorAdapter)
    assert not isinstance(object(), DomainPolicyPack)

    registry = _build_registry()
    assert registry.list_domains() == [DomainKind.INFRASTRUCTURE, DomainKind.COMPLIANCE]
    assert DomainKind.INFRASTRUCTURE in registry
    assert DomainKind.BUSINESS not in registry
    assert registry.get(DomainKind.INFRASTRUCTURE).domain == DomainKind.INFRASTRUCTURE

    with pytest.raises(ValueError, match="already registered"):
        registry.register(registry.get(DomainKind.INFRASTRUCTURE))

    with pytest.raises(KeyError):
        registry.get(DomainKind.OPERATIONS)


def test_domain_pack_rejects_domain_mismatch_and_invalid_adapter() -> None:
    with pytest.raises(ValueError, match="mismatch"):
        DomainPack(
            domain=DomainKind.INFRASTRUCTURE,
            proposal_adapter=MockInfrastructureProposalAdapter(),
            proof_executor=MockComplianceProofExecutor(),
            policy_pack=MockInfrastructurePolicyPack(),
        )

    with pytest.raises(ValidationError, match="ArtifactProposalAdapter"):
        DomainPack(
            domain=DomainKind.INFRASTRUCTURE,
            proposal_adapter=object(),  # type: ignore[arg-type]
            proof_executor=MockInfrastructureProofExecutor(),
            policy_pack=MockInfrastructurePolicyPack(),
        )


def test_proposal_canonical_sha256_digest_is_deterministic() -> None:
    adapter = MockInfrastructureProposalAdapter()
    p1 = adapter.build_proposal(
        target_resource="k8s/deployment/api",
        summary="Scale replicas",
        payload={"replicas": 4, "namespace": "prod"},
        rollback_plan="kubectl rollout undo deployment/api -n prod",
    )
    p2 = adapter.build_proposal(
        target_resource="k8s/deployment/api",
        summary="Scale replicas",
        payload={"namespace": "prod", "replicas": 4},
        rollback_plan="kubectl rollout undo deployment/api -n prod",
    )

    expected_canonical = json.dumps(
        {"namespace": "prod", "replicas": 4}, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    expected_digest = hashlib.sha256(expected_canonical).hexdigest()

    assert len(p1.payload_digest) == 64
    assert p1.payload_digest == expected_digest
    assert p1.payload_digest == p2.payload_digest


def test_evaluate_domain_proposal_enforces_actor_separation() -> None:
    registry = _build_registry()
    infra_pack = registry.get(DomainKind.INFRASTRUCTURE)
    proposal = infra_pack.proposal_adapter.build_proposal(
        target_resource="aws_s3_bucket.logs",
        summary="Enable bucket encryption",
        payload={"environment": "staging", "sse": "AES256"},
        rollback_plan="Revert bucket encryption state",
    )

    with pytest.raises(PermissionError, match="distinct from builder_actor"):
        evaluate_domain_proposal(
            infra_pack,
            proposal,
            run_id="run-sod-1",
            builder_actor="infra-agent-1",
            verifier_actor="infra-agent-1",
        )

    with pytest.raises(PermissionError, match="distinct from builder_actor"):
        evaluate_domain_proposal(
            infra_pack,
            proposal,
            run_id="run-sod-2",
            builder_actor="infra-agent-1",
            verifier_actor="  infra-agent-1  ",
        )


def test_high_risk_infrastructure_proposal_flows_through_release_decision_engine() -> None:
    registry = _build_registry()
    infra_pack = registry.get(DomainKind.INFRASTRUCTURE)
    proposal = infra_pack.proposal_adapter.build_proposal(
        target_resource="aws_security_group.prod_ingress",
        summary="Restrict production ingress to corporate CIDR",
        payload={"environment": "production", "cidr_blocks": ["10.10.0.0/16"]},
        rollback_plan="terraform apply -var-file=previous.tfvars",
    )

    # Blocked without human approval
    blocked = evaluate_domain_proposal(
        infra_pack,
        proposal,
        run_id="run-infra-high-1",
        builder_actor="terraform-agent",
        verifier_actor="infra-verifier-bot",
        obligation_ids=["tf-plan-check", "tf-drift-check"],
    )
    assert isinstance(blocked, ReleaseDecision)
    assert blocked.can_release is False
    assert blocked.outcome == ReleaseOutcome.HUMAN_APPROVAL_REQUIRED
    assert blocked.risk_tier == RiskTier.HIGH
    assert blocked.eip_digest == proposal.payload_digest

    # Builder self-approval in human_approvers is denied
    self_approved = evaluate_domain_proposal(
        infra_pack,
        proposal,
        run_id="run-infra-high-2",
        builder_actor="terraform-agent",
        verifier_actor="infra-verifier-bot",
        human_approvers=["terraform-agent"],
    )
    assert self_approved.can_release is False
    assert self_approved.outcome == ReleaseOutcome.DENIED_POLICY

    # Approved once distinct human guardian signs off
    approved = evaluate_domain_proposal(
        infra_pack,
        proposal,
        run_id="run-infra-high-3",
        builder_actor="terraform-agent",
        verifier_actor="infra-verifier-bot",
        human_approvers=["sre-guardian"],
        obligation_ids=["tf-plan-check", "tf-drift-check"],
    )
    assert approved.can_release is True
    assert approved.outcome == ReleaseOutcome.APPROVED_WITH_HUMAN
    assert approved.recorded_approvers == ["sre-guardian"]

    applied = infra_pack.proposal_adapter.apply_proposal(
        proposal, approved_decision_id=approved.decision_id
    )
    assert applied["status"] == "applied"
    assert applied["approved_decision_id"] == approved.decision_id


def test_critical_compliance_proposal_requires_explicit_auth_and_dual_signoff() -> None:
    registry = _build_registry()
    comp_pack = registry.get(DomainKind.COMPLIANCE)
    proposal = comp_pack.proposal_adapter.build_proposal(
        target_resource="iam_role/prod-auditor",
        summary="Update SOC2-CC6 break-glass access policy",
        payload={"control_family": "SOC2-CC6", "audit_ticket": "AUD-2026-114"},
        rollback_plan="Restore prior IAM policy version v12",
    )

    # Blocked when missing explicit authorization and dual human approvals
    blocked_none = evaluate_domain_proposal(
        comp_pack,
        proposal,
        run_id="run-comp-crit-1",
        builder_actor="compliance-agent",
        verifier_actor="compliance-verifier",
    )
    assert blocked_none.can_release is False
    assert blocked_none.outcome == ReleaseOutcome.RESTRICTED_AUTHORIZATION_REQUIRED
    assert blocked_none.risk_tier == RiskTier.CRITICAL

    # Still blocked with only 1 human approver + authorization ref
    blocked_single = evaluate_domain_proposal(
        comp_pack,
        proposal,
        run_id="run-comp-crit-2",
        builder_actor="compliance-agent",
        verifier_actor="compliance-verifier",
        human_approvers=["ciso-delegate"],
        explicit_authorization_ref="CAB-2026-099",
    )
    assert blocked_single.can_release is False
    assert blocked_single.outcome == ReleaseOutcome.RESTRICTED_AUTHORIZATION_REQUIRED

    # Succeeds once 2 distinct human approvers + explicit authorization ref are supplied
    approved = evaluate_domain_proposal(
        comp_pack,
        proposal,
        run_id="run-comp-crit-3",
        builder_actor="compliance-agent",
        verifier_actor="compliance-verifier",
        human_approvers=["ciso-delegate", "secops-lead"],
        explicit_authorization_ref="CAB-2026-099",
        obligation_ids=["soc2-cc6-control-check"],
    )
    assert approved.can_release is True
    assert approved.outcome == ReleaseOutcome.APPROVED_RESTRICTED
    assert approved.explicit_authorization_ref == "CAB-2026-099"
    assert approved.recorded_approvers == ["ciso-delegate", "secops-lead"]


def test_validation_errors_and_non_isolated_proofs_fail_closed() -> None:
    registry = _build_registry()
    infra_pack = registry.get(DomainKind.INFRASTRUCTURE)

    invalid_proposal = infra_pack.proposal_adapter.build_proposal(
        target_resource="aws_security_group.prod_ingress",
        summary="Open wildcard ingress without rollback",
        payload={"environment": "production", "cidr_blocks": ["0.0.0.0/0"]},
        rollback_plan="",
    )
    denied = evaluate_domain_proposal(
        infra_pack,
        invalid_proposal,
        run_id="run-invalid-1",
        builder_actor="terraform-agent",
        verifier_actor="infra-verifier-bot",
        human_approvers=["sre-guardian"],
    )
    assert denied.can_release is False
    assert denied.outcome == ReleaseOutcome.DENIED_POLICY
    assert any("rollback_plan" in reason for reason in denied.reasons)
    assert any("0.0.0.0/0" in reason for reason in denied.reasons)

    # Non-isolated proof execution cannot pass HIGH-risk independent verification gate
    non_isolated_pack = DomainPack(
        domain=DomainKind.INFRASTRUCTURE,
        proposal_adapter=MockInfrastructureProposalAdapter(),
        proof_executor=MockInfrastructureProofExecutor(isolated=False),
        policy_pack=MockInfrastructurePolicyPack(),
    )
    valid_proposal = non_isolated_pack.proposal_adapter.build_proposal(
        target_resource="aws_security_group.prod_ingress",
        summary="Restrict ingress",
        payload={"environment": "production", "cidr_blocks": ["10.10.0.0/16"]},
        rollback_plan="terraform apply -var-file=previous.tfvars",
    )
    blocked_isolation = evaluate_domain_proposal(
        non_isolated_pack,
        valid_proposal,
        run_id="run-non-iso-1",
        builder_actor="terraform-agent",
        verifier_actor="infra-verifier-bot",
        human_approvers=["sre-guardian"],
    )
    assert blocked_isolation.can_release is False
    assert blocked_isolation.outcome == ReleaseOutcome.BLOCKED_INSUFFICIENT_ASSURANCE

    # Cross-domain proposal mismatch raises ValueError
    comp_pack = registry.get(DomainKind.COMPLIANCE)
    with pytest.raises(ValueError, match="does not match pack domain"):
        evaluate_domain_proposal(
            comp_pack,
            valid_proposal,
            run_id="run-mismatch-1",
            builder_actor="compliance-agent",
            verifier_actor="compliance-verifier",
        )
