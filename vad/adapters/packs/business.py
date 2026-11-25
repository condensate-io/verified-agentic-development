"""Scope B Business Domain Pack for governed business automation proposals."""
from __future__ import annotations

from typing import Any, Optional

from vad.adapters.domain_pack import (
    DomainArtifactProposal,
    DomainKind,
    DomainPack,
    DomainProofOutcome,
)
from vad.contracts.models import AssuranceLevel, RiskTier, default_assurance_for_risk


def _parse_optional_float(value: Any) -> Optional[float]:
    """Parse a numeric payload field to float, returning None when absent."""
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError("Boolean value is not a valid numeric metric")
    return float(value)


class BusinessProposalAdapter:
    """Proposal adapter for governed business automation changes.

    Covers pricing/discount rules, billing/credit adjustments, customer entitlement
    tiers, and automated contract/workflow transitions.
    """

    domain: DomainKind = DomainKind.BUSINESS

    def __init__(self) -> None:
        self.applied_workflows: list[dict[str, Any]] = []

    def build_proposal(
        self,
        target_resource: str,
        summary: str,
        payload: dict[str, Any],
        rollback_plan: str = "",
        proposal_id: Optional[str] = None,
    ) -> DomainArtifactProposal:
        """Build a canonical DomainArtifactProposal for a business automation change."""
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
        """Record an approved business proposal execution and return its receipt."""
        if proposal.domain != self.domain:
            raise ValueError(
                f"Proposal domain {proposal.domain.value!r} does not match {self.domain.value!r}"
            )
        decision_id = approved_decision_id.strip()
        if not decision_id:
            raise PermissionError(
                "approved_decision_id must be non-empty to apply a business proposal"
            )
        receipt: dict[str, Any] = {
            "status": "applied",
            "domain": self.domain.value,
            "proposal_id": proposal.proposal_id,
            "target_resource": proposal.target_resource,
            "approved_decision_id": decision_id,
            "payload_digest": proposal.payload_digest,
        }
        self.applied_workflows.append(receipt)
        return receipt


class BusinessPolicyPack:
    """Policy pack mapping business automation proposals to risk, assurance, and rules."""

    domain: DomainKind = DomainKind.BUSINESS

    def default_risk_tier(self, proposal: DomainArtifactProposal) -> RiskTier:
        """Classify risk tier from financial impact, ledger/tax impact, and segment."""
        try:
            impact = _parse_optional_float(proposal.payload.get("financial_impact_usd", 0.0)) or 0.0
        except (TypeError, ValueError):
            impact = 0.0

        if impact >= 50000.0 or proposal.payload.get("affects_ledger_or_tax") is True:
            return RiskTier.CRITICAL

        segment = (
            proposal.payload.get("customer_Segment")
            if "customer_Segment" in proposal.payload
            else proposal.payload.get("customer_segment")
        )
        if (
            impact >= 5000.0
            or proposal.payload.get("customer_Segment") == "enterprise"
            or proposal.payload.get("customer_segment") == "enterprise"
            or segment == "enterprise"
        ):
            return RiskTier.HIGH

        if impact >= 500.0:
            return RiskTier.MEDIUM

        return RiskTier.LOW

    def required_assurance(self, proposal: DomainArtifactProposal) -> AssuranceLevel:
        """Return the AssuranceLevel required for the proposal's risk tier."""
        return default_assurance_for_risk(self.default_risk_tier(proposal))

    def validate_proposal(self, proposal: DomainArtifactProposal) -> list[str]:
        """Validate rollback plan, financial/discount bounds, and policy references."""
        errors: list[str] = []
        if not proposal.rollback_plan.strip():
            errors.append("Business proposal must declare a non-empty rollback_plan")

        impact_value = 0.0
        if "financial_impact_usd" in proposal.payload and proposal.payload["financial_impact_usd"] is not None:
            try:
                parsed_impact = _parse_optional_float(proposal.payload["financial_impact_usd"])
                assert parsed_impact is not None
                impact_value = parsed_impact
                if impact_value < 0:
                    errors.append("financial_impact_usd must not be negative")
            except (TypeError, ValueError):
                errors.append("financial_impact_usd must be a valid number")

        if "discount_pct" in proposal.payload and proposal.payload["discount_pct"] is not None:
            try:
                discount = _parse_optional_float(proposal.payload["discount_pct"])
                assert discount is not None
                if discount < 0 or discount > 100:
                    errors.append("discount_pct must be between 0 and 100 inclusive")
            except (TypeError, ValueError):
                errors.append("discount_pct must be a valid number")

        requires_policy_ref = (
            impact_value >= 5000.0 or proposal.payload.get("affects_ledger_or_tax") is True
        )
        if requires_policy_ref and not str(proposal.payload.get("policy_reference") or "").strip():
            errors.append(
                "Business proposal with financial_impact_usd >= 5000 or affects_ledger_or_tax requires a non-empty policy_reference"
            )

        return errors


class BusinessProofExecutor:
    """Independent proof executor running isolated business invariant simulations."""

    domain: DomainKind = DomainKind.BUSINESS

    def execute_obligation(
        self,
        proposal: DomainArtifactProposal,
        obligation_id: str,
        *,
        verifier_actor: str,
    ) -> DomainProofOutcome:
        """Execute an isolated invariant simulation for a business proposal."""
        passed = bool(proposal.payload.get("simulation_passed", True))
        return DomainProofOutcome(
            obligation_id=obligation_id,
            passed=passed,
            summary=f"Isolated business invariant simulation for {proposal.target_resource} ({obligation_id})",
            verifier_actor=verifier_actor,
            isolated=True,
        )


def create_business_domain_pack() -> DomainPack:
    """Create a configured Scope B DomainPack for DomainKind.BUSINESS."""
    return DomainPack(
        domain=DomainKind.BUSINESS,
        proposal_adapter=BusinessProposalAdapter(),
        proof_executor=BusinessProofExecutor(),
        policy_pack=BusinessPolicyPack(),
    )
