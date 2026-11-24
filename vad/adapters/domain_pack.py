"""Scope B extensibility seam allowing non-coding autonomous agents to reuse VAD core."""
from __future__ import annotations

from enum import Enum
import hashlib
import json
from typing import Any, Optional, Protocol, Sequence, runtime_checkable
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from vad.contracts.models import AssuranceLevel, RiskTier
from vad.policy.autonomy_matrix import (
    AutonomyEvaluationInput,
    AutonomyMatrixEvaluator,
    PolicyStage,
)
from vad.release.decision import ReleaseDecision, ReleaseDecisionEngine


def compute_canonical_digest(data: Any) -> str:
    """Compute a deterministic SHA-256 hex digest over canonical JSON."""
    canonical = json.dumps(data, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class DomainKind(str, Enum):
    """Execution domains across Scope A (software delivery) and Scope B (autonomous control)."""

    SOFTWARE_DELIVERY = "software_delivery"
    INFRASTRUCTURE = "infrastructure"
    OPERATIONS = "operations"
    BUSINESS = "business"
    COMPLIANCE = "compliance"


class DomainArtifactProposal(BaseModel):
    """Domain-neutral artifact proposal carrying a canonical payload digest and rollback plan."""

    proposal_id: str = Field(default_factory=lambda: f"prop-{uuid4().hex[:12]}", min_length=1)
    domain: DomainKind
    target_resource: str = Field(..., min_length=1)
    summary: str = Field(..., min_length=1)
    payload: dict[str, Any] = Field(default_factory=dict)
    payload_digest: str = ""
    rollback_plan: str = ""

    @model_validator(mode="after")
    def _populate_payload_digest(self) -> "DomainArtifactProposal":
        if not self.payload_digest:
            self.payload_digest = compute_canonical_digest(self.payload)
        return self


class DomainProofOutcome(BaseModel):
    """Independent verification outcome for a single domain proof obligation."""

    obligation_id: str = Field(..., min_length=1)
    passed: bool
    summary: str = Field(..., min_length=1)
    verifier_actor: str = Field(..., min_length=1)
    isolated: bool = True
    evidence_digest: str = ""

    @model_validator(mode="after")
    def _populate_evidence_digest(self) -> "DomainProofOutcome":
        if not self.evidence_digest:
            self.evidence_digest = compute_canonical_digest(
                {
                    "obligation_id": self.obligation_id,
                    "passed": self.passed,
                    "summary": self.summary,
                    "verifier_actor": self.verifier_actor,
                    "isolated": self.isolated,
                }
            )
        return self


@runtime_checkable
class ArtifactProposalAdapter(Protocol):
    """Protocol for constructing and applying domain-specific artifact proposals."""

    domain: DomainKind

    def build_proposal(
        self,
        target_resource: str,
        summary: str,
        payload: dict[str, Any],
        rollback_plan: str = "",
        proposal_id: Optional[str] = None,
    ) -> DomainArtifactProposal:
        ...

    def apply_proposal(
        self,
        proposal: DomainArtifactProposal,
        *,
        approved_decision_id: str,
    ) -> dict[str, Any]:
        ...


@runtime_checkable
class ProofExecutorAdapter(Protocol):
    """Protocol for executing independent proof obligations for a domain proposal."""

    domain: DomainKind

    def execute_obligation(
        self,
        proposal: DomainArtifactProposal,
        obligation_id: str,
        *,
        verifier_actor: str,
    ) -> DomainProofOutcome:
        ...


@runtime_checkable
class DomainPolicyPack(Protocol):
    """Protocol mapping domain proposals to VAD risk, assurance, and validation rules."""

    domain: DomainKind

    def default_risk_tier(self, proposal: DomainArtifactProposal) -> RiskTier:
        ...

    def required_assurance(self, proposal: DomainArtifactProposal) -> AssuranceLevel:
        ...

    def validate_proposal(self, proposal: DomainArtifactProposal) -> list[str]:
        ...


class DomainPack(BaseModel):
    """Bundled Scope B adapters registered for a single DomainKind."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    domain: DomainKind
    proposal_adapter: ArtifactProposalAdapter
    proof_executor: ProofExecutorAdapter
    policy_pack: DomainPolicyPack

    @model_validator(mode="after")
    def _validate_adapters_and_domain_alignment(self) -> "DomainPack":
        if not isinstance(self.proposal_adapter, ArtifactProposalAdapter):
            raise TypeError("proposal_adapter must implement ArtifactProposalAdapter")
        if not isinstance(self.proof_executor, ProofExecutorAdapter):
            raise TypeError("proof_executor must implement ProofExecutorAdapter")
        if not isinstance(self.policy_pack, DomainPolicyPack):
            raise TypeError("policy_pack must implement DomainPolicyPack")

        mismatches: list[str] = []
        if self.proposal_adapter.domain != self.domain:
            mismatches.append(f"proposal_adapter.domain={self.proposal_adapter.domain!r}")
        if self.proof_executor.domain != self.domain:
            mismatches.append(f"proof_executor.domain={self.proof_executor.domain!r}")
        if self.policy_pack.domain != self.domain:
            mismatches.append(f"policy_pack.domain={self.policy_pack.domain!r}")
        if mismatches:
            raise ValueError(
                f"DomainPack domain {self.domain.value!r} mismatch: {', '.join(mismatches)}"
            )
        return self


class DomainPackRegistry:
    """In-memory registry mapping DomainKind values to DomainPack instances."""

    def __init__(self) -> None:
        self._packs: dict[DomainKind, DomainPack] = {}

    def register(self, pack: DomainPack, *, replace: bool = False) -> DomainPack:
        """Register a DomainPack; raise ValueError on duplicate unless replace=True."""
        if pack.domain in self._packs and not replace:
            raise ValueError(f"DomainPack already registered for domain {pack.domain.value!r}")
        self._packs[pack.domain] = pack
        return pack

    def get(self, domain: DomainKind) -> DomainPack:
        """Return the registered DomainPack for domain, or raise KeyError."""
        if domain not in self._packs:
            raise KeyError(f"No DomainPack registered for domain {domain.value!r}")
        return self._packs[domain]

    def list_domains(self) -> list[DomainKind]:
        """Return registered domains in registration order."""
        return list(self._packs.keys())

    def __contains__(self, domain: DomainKind) -> bool:
        return domain in self._packs


def evaluate_domain_proposal(
    pack: DomainPack,
    proposal: DomainArtifactProposal,
    *,
    run_id: str,
    builder_actor: str,
    verifier_actor: str,
    human_approvers: Optional[Sequence[str]] = None,
    explicit_authorization_ref: Optional[str] = None,
    obligation_ids: Optional[Sequence[str]] = None,
    stage: PolicyStage = PolicyStage.DEPLOY,
    evidence_fresh: bool = True,
    drift_detected: bool = False,
    decision_engine: Optional[ReleaseDecisionEngine] = None,
) -> ReleaseDecision:
    """Evaluate a non-coding domain proposal through VAD's AutonomyMatrixEvaluator and ReleaseDecisionEngine."""
    builder = builder_actor.strip()
    verifier = verifier_actor.strip()
    if not builder or not verifier:
        raise PermissionError("builder_actor and verifier_actor must be non-empty")
    if builder == verifier:
        raise PermissionError(
            "verifier_actor must be distinct from builder_actor for independent verification"
        )

    if proposal.domain != pack.domain:
        raise ValueError(
            f"Proposal domain {proposal.domain.value!r} does not match pack domain {pack.domain.value!r}"
        )

    target_obligations = (
        list(obligation_ids)
        if obligation_ids is not None
        else [f"{pack.domain.value}-default-obligation"]
    )
    if not target_obligations:
        raise ValueError("At least one obligation_id is required to evaluate a domain proposal")

    validation_errors = list(pack.policy_pack.validate_proposal(proposal))
    risk_tier = pack.policy_pack.default_risk_tier(proposal)
    required_assurance = pack.policy_pack.required_assurance(proposal)

    outcomes: list[DomainProofOutcome] = []
    for obligation_id in target_obligations:
        outcome = pack.proof_executor.execute_obligation(
            proposal,
            obligation_id,
            verifier_actor=verifier,
        )
        outcome_verifier = outcome.verifier_actor.strip()
        if outcome_verifier == builder:
            raise PermissionError(
                "DomainProofOutcome verifier_actor must be distinct from builder_actor"
            )
        if outcome_verifier != verifier:
            raise PermissionError(
                f"DomainProofOutcome verifier_actor {outcome.verifier_actor!r} does not match {verifier!r}"
            )
        outcomes.append(outcome)

    all_proofs_passed = all(outcome.passed for outcome in outcomes)
    all_isolated = all(outcome.isolated for outcome in outcomes)
    verification_passed = (len(validation_errors) == 0) and all_proofs_passed
    independent_verifier = (builder != verifier) and all_isolated
    assurance_achieved = (
        required_assurance if (verification_passed and all_isolated) else AssuranceLevel.A1_BASIC
    )

    evidence_digest = compute_canonical_digest(
        {
            "domain": pack.domain.value,
            "proposal_id": proposal.proposal_id,
            "payload_digest": proposal.payload_digest,
            "validation_errors": validation_errors,
            "outcomes": [outcome.model_dump(mode="json") for outcome in outcomes],
        }
    )

    engine = decision_engine or ReleaseDecisionEngine(
        evaluator=AutonomyMatrixEvaluator(use_opa=False)
    )
    eval_input = AutonomyEvaluationInput(
        risk_tier=risk_tier,
        stage=stage,
        verification_passed=verification_passed,
        assurance_achieved=assurance_achieved,
        independent_verifier=independent_verifier,
        evidence_fresh=evidence_fresh,
        drift_detected=drift_detected,
        human_approvers=list(human_approvers or []),
        builder_actor=builder,
        verifier_actor=verifier,
        explicit_authorization_ref=explicit_authorization_ref,
    )
    decision = engine.decide(
        run_id=run_id,
        eip_digest=proposal.payload_digest,
        evidence_digest=evidence_digest,
        eval_input=eval_input,
    )
    if validation_errors and not decision.can_release:
        return decision.model_copy(update={"reasons": [*decision.reasons, *validation_errors]})
    return decision
