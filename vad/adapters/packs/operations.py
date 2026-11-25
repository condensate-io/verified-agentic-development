"""Built-in Scope B Operations domain pack (SLO remediation, traffic shifts, runbooks)."""
from __future__ import annotations

from typing import Any, Optional

from vad.adapters.domain_pack import (
    DomainArtifactProposal,
    DomainKind,
    DomainPack,
    DomainProofOutcome,
)
from vad.contracts.models import AssuranceLevel, RiskTier, default_assurance_for_risk

_PROD_ENVS = frozenset({"production", "prod"})
_STAGING_ENVS = frozenset({"staging", "preprod"})
_CRITICAL_ACTIONS = frozenset({"region_failover", "data_purge", "break_glass"})
_MEDIUM_ACTIONS = frozenset({"traffic_shift", "restart_fleet"})


def _parse_blast_radius(value: Any) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _has_non_empty_field(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (dict, list, tuple, set)):
        return len(value) > 0
    return True


class OperationsProposalAdapter:
    """Normalizes and executes SLO remediation, traffic shift, scaling, and runbook proposals."""

    domain: DomainKind = DomainKind.OPERATIONS

    def __init__(self) -> None:
        self.executed_runbooks: list[dict[str, Any]] = []

    def build_proposal(
        self,
        target_resource: str,
        summary: str,
        payload: dict[str, Any],
        rollback_plan: str = "",
        proposal_id: Optional[str] = None,
    ) -> DomainArtifactProposal:
        normalized = dict(payload)

        if "environment" in normalized and isinstance(normalized["environment"], str):
            normalized["environment"] = normalized["environment"].strip().lower()
        elif "env" in normalized and isinstance(normalized["env"], str):
            normalized["environment"] = normalized["env"].strip().lower()

        if "action_type" in normalized and isinstance(normalized["action_type"], str):
            normalized["action_type"] = normalized["action_type"].strip().lower()

        kwargs: dict[str, Any] = {
            "domain": self.domain,
            "target_resource": target_resource.strip(),
            "summary": summary.strip(),
            "payload": normalized,
            "rollback_plan": rollback_plan.strip(),
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
        if proposal.domain != self.domain:
            raise ValueError(
                f"Expected proposal domain {self.domain.value!r}, got {proposal.domain.value!r}"
            )
        decision_id = approved_decision_id.strip()
        if not decision_id:
            raise PermissionError(
                "approved_decision_id must be non-empty before executing operations runbook"
            )

        receipt: dict[str, Any] = {
            "status": "executed",
            "domain": self.domain.value,
            "proposal_id": proposal.proposal_id,
            "target_resource": proposal.target_resource,
            "approved_decision_id": decision_id,
            "payload_digest": proposal.payload_digest,
            "action_type": str(proposal.payload.get("action_type", "runbook_execution")),
        }
        self.executed_runbooks.append(receipt)
        return receipt


class OperationsPolicyPack:
    """Maps operational runbook proposals to VAD risk tiers, assurance levels, and safety checks."""

    domain: DomainKind = DomainKind.OPERATIONS

    def default_risk_tier(self, proposal: DomainArtifactProposal) -> RiskTier:
        payload = proposal.payload
        environment = str(payload.get("environment", "")).strip().lower()
        action_type = str(payload.get("action_type", "")).strip().lower()
        blast_radius = _parse_blast_radius(payload.get("blast_radius_pct", 0))
        effective_radius = blast_radius if blast_radius is not None else 0.0

        if effective_radius > 50 or action_type in _CRITICAL_ACTIONS:
            return RiskTier.CRITICAL
        if environment in _PROD_ENVS or effective_radius > 10:
            return RiskTier.HIGH
        if environment in _STAGING_ENVS or action_type in _MEDIUM_ACTIONS:
            return RiskTier.MEDIUM
        return RiskTier.LOW

    def required_assurance(self, proposal: DomainArtifactProposal) -> AssuranceLevel:
        return default_assurance_for_risk(self.default_risk_tier(proposal))

    def validate_proposal(self, proposal: DomainArtifactProposal) -> list[str]:
        errors: list[str] = []
        payload = proposal.payload

        if not proposal.rollback_plan.strip():
            errors.append("Operations proposal must declare a non-empty rollback_plan")

        if "blast_radius_pct" in payload:
            blast_radius = _parse_blast_radius(payload["blast_radius_pct"])
            if blast_radius is None or blast_radius < 0 or blast_radius > 100:
                errors.append("Operations proposal blast_radius_pct must be between 0 and 100")

        environment = str(payload.get("environment", "")).strip().lower()
        if environment in _PROD_ENVS:
            if not _has_non_empty_field(payload.get("slo_target")):
                errors.append("Production operations proposal requires a non-empty slo_target")
            if not _has_non_empty_field(payload.get("health_check_probe")):
                errors.append(
                    "Production operations proposal requires a non-empty health_check_probe"
                )

        return errors


class OperationsProofExecutor:
    """Executes deterministic isolated pre-flight health checks for operational proposals."""

    domain: DomainKind = DomainKind.OPERATIONS

    def __init__(self, policy_pack: Optional[OperationsPolicyPack] = None) -> None:
        self._policy_pack = policy_pack or OperationsPolicyPack()

    def execute_obligation(
        self,
        proposal: DomainArtifactProposal,
        obligation_id: str,
        *,
        verifier_actor: str,
    ) -> DomainProofOutcome:
        verifier = verifier_actor.strip()
        obligation = obligation_id.strip()
        if not verifier:
            raise ValueError("verifier_actor must be non-empty")
        if not obligation:
            raise ValueError("obligation_id must be non-empty")

        preflight_healthy = proposal.payload.get("preflight_healthy", True) is True
        policy_errors = self._policy_pack.validate_proposal(proposal)
        passed = preflight_healthy and not policy_errors

        if passed:
            summary = f"Pre-flight health check passed for {proposal.target_resource} ({obligation})"
        else:
            failures: list[str] = []
            if not preflight_healthy:
                failures.append("preflight_healthy is False")
            failures.extend(policy_errors)
            summary = (
                f"Pre-flight health check failed for {proposal.target_resource}: "
                + "; ".join(failures)
            )

        return DomainProofOutcome(
            obligation_id=obligation,
            passed=passed,
            summary=summary,
            verifier_actor=verifier,
            isolated=True,
        )


def create_operations_domain_pack() -> DomainPack:
    """Construct the built-in Scope B Operations DomainPack."""
    policy_pack = OperationsPolicyPack()
    return DomainPack(
        domain=DomainKind.OPERATIONS,
        proposal_adapter=OperationsProposalAdapter(),
        proof_executor=OperationsProofExecutor(policy_pack=policy_pack),
        policy_pack=policy_pack,
    )
