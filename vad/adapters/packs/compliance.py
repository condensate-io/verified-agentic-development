"""Scope B Compliance Domain Pack for governed audit and control proposals."""
from __future__ import annotations

from typing import Any, Optional

from vad.adapters.domain_pack import (
    DomainArtifactProposal,
    DomainKind,
    DomainPack,
    DomainProofOutcome,
)
from vad.contracts.models import AssuranceLevel, RiskTier, default_assurance_for_risk

_CRITICAL_CONTROL_FAMILIES = frozenset({"SOC2-CC6", "ISO27001-A.9", "GDPR-ERASURE"})
_HIGH_FRAMEWORKS = frozenset({"SOC2", "ISO27001", "HIPAA", "PCI-DSS"})


class ComplianceProposalAdapter:
    """Proposal adapter for compliance attestations, policies, access reviews, and exceptions."""

    domain: DomainKind = DomainKind.COMPLIANCE

    def __init__(self) -> None:
        self.recorded_attestations: list[dict[str, Any]] = []

    def build_proposal(
        self,
        target_resource: str,
        summary: str,
        payload: dict[str, Any],
        rollback_plan: str = "",
        proposal_id: Optional[str] = None,
    ) -> DomainArtifactProposal:
        """Build a canonical DomainArtifactProposal for a compliance action."""
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
        """Record an approved compliance attestation and return its receipt."""
        if proposal.domain != self.domain:
            raise ValueError(
                f"Proposal domain {proposal.domain.value!r} does not match {self.domain.value!r}"
            )
        decision_id = approved_decision_id.strip()
        if not decision_id:
            raise PermissionError(
                "approved_decision_id must be non-empty to record a compliance attestation"
            )
        receipt: dict[str, Any] = {
            "status": "attested",
            "domain": self.domain.value,
            "proposal_id": proposal.proposal_id,
            "target_resource": proposal.target_resource,
            "approved_decision_id": decision_id,
            "payload_digest": proposal.payload_digest,
        }
        self.recorded_attestations.append(receipt)
        return receipt


class CompliancePolicyPack:
    """Policy pack mapping compliance proposals to risk, assurance, and audit rules."""

    domain: DomainKind = DomainKind.COMPLIANCE

    def default_risk_tier(self, proposal: DomainArtifactProposal) -> RiskTier:
        """Classify compliance risk tier by control family, exception flag, and framework."""
        control_family = proposal.payload.get("control_family")
        if control_family in _CRITICAL_CONTROL_FAMILIES or proposal.payload.get("exception_request") is True:
            return RiskTier.CRITICAL

        framework = proposal.payload.get("framework")
        if framework in _HIGH_FRAMEWORKS:
            return RiskTier.HIGH

        if proposal.payload.get("internal_only") is True:
            return RiskTier.MEDIUM

        return RiskTier.HIGH

    def required_assurance(self, proposal: DomainArtifactProposal) -> AssuranceLevel:
        """Return the AssuranceLevel required for the proposal's risk tier."""
        return default_assurance_for_risk(self.default_risk_tier(proposal))

    def validate_proposal(self, proposal: DomainArtifactProposal) -> list[str]:
        """Validate rollback plan, audit ticket, and bounded policy exception fields."""
        errors: list[str] = []
        if not proposal.rollback_plan.strip():
            errors.append("Compliance proposal must declare a non-empty rollback_plan")

        if not str(proposal.payload.get("audit_ticket") or "").strip():
            errors.append("Compliance proposal requires a non-empty audit_ticket")

        if proposal.payload.get("exception_request") is True:
            if not str(proposal.payload.get("expires_at") or "").strip():
                errors.append("Compliance exception_request requires a non-empty expires_at")
            if not str(proposal.payload.get("compensating_control") or "").strip():
                errors.append(
                    "Compliance exception_request requires a non-empty compensating_control"
                )

        return errors


class ComplianceProofExecutor:
    """Independent proof executor verifying compliance control evidence."""

    domain: DomainKind = DomainKind.COMPLIANCE

    def execute_obligation(
        self,
        proposal: DomainArtifactProposal,
        obligation_id: str,
        *,
        verifier_actor: str,
    ) -> DomainProofOutcome:
        """Verify audit ticket presence and control check status in isolation."""
        has_audit_ticket = bool(str(proposal.payload.get("audit_ticket") or "").strip())
        control_passed = bool(proposal.payload.get("control_check_passed", True))
        passed = has_audit_ticket and control_passed
        return DomainProofOutcome(
            obligation_id=obligation_id,
            passed=passed,
            summary=f"Compliance control evidence verification for {proposal.target_resource} ({obligation_id})",
            verifier_actor=verifier_actor,
            isolated=True,
        )


def create_compliance_domain_pack() -> DomainPack:
    """Create a configured Scope B DomainPack for DomainKind.COMPLIANCE."""
    return DomainPack(
        domain=DomainKind.COMPLIANCE,
        proposal_adapter=ComplianceProposalAdapter(),
        proof_executor=ComplianceProofExecutor(),
        policy_pack=CompliancePolicyPack(),
    )
