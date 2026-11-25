"""Built-in Scope B Infrastructure domain pack (Terraform, Kubernetes, IAM, Network)."""
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
_CRITICAL_RESOURCE_KINDS = frozenset({"iam", "kms", "network_firewall", "database_cluster"})
_WILDCARD_CIDRS = frozenset({"0.0.0.0/0", "::/0"})


def _normalize_str_list(values: Any) -> list[str]:
    if values is None:
        return []
    if isinstance(values, str):
        stripped = values.strip()
        return [stripped] if stripped else []
    if isinstance(values, set):
        return sorted(str(item).strip() for item in values if str(item).strip())
    if isinstance(values, (list, tuple)):
        return [str(item).strip() for item in values if str(item).strip()]
    return []


def _extract_k8s_security_context(manifest: dict[str, Any]) -> dict[str, Any]:
    """Extract container security context flags from a Kubernetes manifest dictionary."""
    containers: list[dict[str, Any]] = []
    spec = manifest.get("spec")
    if isinstance(spec, dict):
        if isinstance(spec.get("containers"), list):
            containers.extend(c for c in spec["containers"] if isinstance(c, dict))
        template = spec.get("template")
        if isinstance(template, dict) and isinstance(template.get("spec"), dict):
            tpl_spec = template["spec"]
            if isinstance(tpl_spec.get("containers"), list):
                containers.extend(c for c in tpl_spec["containers"] if isinstance(c, dict))

    for container in containers:
        sec_ctx = container.get("securityContext") or container.get("security_context")
        if isinstance(sec_ctx, dict) and sec_ctx.get("privileged") is True:
            return {"privileged": True}
    return {}


def _has_privileged_k8s_container(security_context: Any) -> bool:
    """Return True if any container or nested security context specifies privileged=True."""
    if isinstance(security_context, dict):
        if security_context.get("privileged") is True:
            return True
        containers = security_context.get("containers")
        if isinstance(containers, list):
            return any(_has_privileged_k8s_container(item) for item in containers)
        nested = security_context.get("security_context") or security_context.get("securityContext")
        if nested is not None:
            return _has_privileged_k8s_container(nested)
        return False
    if isinstance(security_context, list):
        return any(_has_privileged_k8s_container(item) for item in security_context)
    return False


class InfrastructureProposalAdapter:
    """Normalizes and applies Terraform, Kubernetes, IAM, and network proposals."""

    domain: DomainKind = DomainKind.INFRASTRUCTURE

    def __init__(self) -> None:
        self.applied_history: list[dict[str, Any]] = []

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

        if "resource_kind" in normalized and isinstance(normalized["resource_kind"], str):
            normalized["resource_kind"] = normalized["resource_kind"].strip().lower()

        for list_key in ("cidr_blocks", "iam_principals", "iam_actions"):
            if list_key in normalized:
                normalized[list_key] = _normalize_str_list(normalized[list_key])

        if (
            "k8s_manifest" in normalized
            and isinstance(normalized["k8s_manifest"], dict)
            and "k8s_security_context" not in normalized
        ):
            extracted = _extract_k8s_security_context(normalized["k8s_manifest"])
            if extracted:
                normalized["k8s_security_context"] = extracted

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
                "approved_decision_id must be non-empty before applying infrastructure proposal"
            )

        receipt: dict[str, Any] = {
            "status": "applied",
            "domain": self.domain.value,
            "proposal_id": proposal.proposal_id,
            "target_resource": proposal.target_resource,
            "approved_decision_id": decision_id,
            "payload_digest": proposal.payload_digest,
        }
        self.applied_history.append(receipt)
        return receipt


class InfrastructurePolicyPack:
    """Maps infrastructure proposals to VAD risk tiers, assurance levels, and safety checks."""

    domain: DomainKind = DomainKind.INFRASTRUCTURE

    def default_risk_tier(self, proposal: DomainArtifactProposal) -> RiskTier:
        payload = proposal.payload
        environment = str(payload.get("environment", "")).strip().lower()
        resource_kind = str(payload.get("resource_kind", "")).strip().lower()

        if (
            payload.get("destructive") is True
            or payload.get("iam_privilege_escalation") is True
            or (environment in _PROD_ENVS and resource_kind in _CRITICAL_RESOURCE_KINDS)
        ):
            return RiskTier.CRITICAL
        if environment in _PROD_ENVS:
            return RiskTier.HIGH
        if environment in _STAGING_ENVS:
            return RiskTier.MEDIUM
        return RiskTier.LOW

    def required_assurance(self, proposal: DomainArtifactProposal) -> AssuranceLevel:
        return default_assurance_for_risk(self.default_risk_tier(proposal))

    def validate_proposal(self, proposal: DomainArtifactProposal) -> list[str]:
        errors: list[str] = []
        payload = proposal.payload

        if not proposal.rollback_plan.strip():
            errors.append("Infrastructure proposal must declare a non-empty rollback_plan")

        if payload.get("public_edge_approved") is not True:
            cidrs = _normalize_str_list(payload.get("cidr_blocks"))
            bad_cidrs = [cidr for cidr in cidrs if cidr in _WILDCARD_CIDRS]
            if bad_cidrs:
                errors.append(
                    f"Wildcard ingress {', '.join(bad_cidrs)} is forbidden without public_edge_approved=True"
                )

        if "*" in _normalize_str_list(payload.get("iam_principals")):
            errors.append("Wildcard IAM principal '*' is forbidden")

        if "*" in _normalize_str_list(payload.get("iam_actions")):
            errors.append("Wildcard IAM action '*' is forbidden")

        if _has_privileged_k8s_container(payload.get("k8s_security_context", {})):
            errors.append("Kubernetes privileged container (privileged: True) is forbidden")

        return errors


class InfrastructureProofExecutor:
    """Executes deterministic isolated infrastructure verification obligations."""

    domain: DomainKind = DomainKind.INFRASTRUCTURE

    def __init__(self, policy_pack: Optional[InfrastructurePolicyPack] = None) -> None:
        self._policy_pack = policy_pack or InfrastructurePolicyPack()

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

        plan_checks_passed = proposal.payload.get("plan_checks_passed", True) is True
        policy_errors = self._policy_pack.validate_proposal(proposal)
        passed = plan_checks_passed and not policy_errors

        if passed:
            summary = f"Isolated infrastructure check passed for {proposal.target_resource} ({obligation})"
        else:
            failures: list[str] = []
            if not plan_checks_passed:
                failures.append("plan_checks_passed is False")
            failures.extend(policy_errors)
            summary = (
                f"Isolated infrastructure check failed for {proposal.target_resource}: "
                + "; ".join(failures)
            )

        return DomainProofOutcome(
            obligation_id=obligation,
            passed=passed,
            summary=summary,
            verifier_actor=verifier,
            isolated=True,
        )


def create_infrastructure_domain_pack() -> DomainPack:
    """Construct the built-in Scope B Infrastructure DomainPack."""
    policy_pack = InfrastructurePolicyPack()
    return DomainPack(
        domain=DomainKind.INFRASTRUCTURE,
        proposal_adapter=InfrastructureProposalAdapter(),
        proof_executor=InfrastructureProofExecutor(policy_pack=policy_pack),
        policy_pack=policy_pack,
    )
