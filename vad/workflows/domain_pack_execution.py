"""Governed execution runner for Scope B Domain Packs through VAD's unmodified core."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from vad.adapters.domain_pack import (
    DomainArtifactProposal,
    DomainKind,
    DomainPackRegistry,
    DomainProofOutcome,
    evaluate_domain_proposal,
)
from vad.adapters.packs import create_default_domain_pack_registry
from vad.feedback.publisher import LearningLoopPublisher
from vad.memory.contracts import FactType
from vad.memory.gateway import MemoryGateway
from vad.memory.providers.null import NullMemoryProvider
from vad.policy.autonomy_matrix import PolicyStage
from vad.release.decision import ReleaseDecision, ReleaseDecisionEngine, ReleaseOutcome
from vad.review.dsse_attestation import DSSEAttestor, DSSEEnvelope
from vad.signing.ed25519_local import LocalEd25519KeyProvider

try:
    from vad.server.db.store import StoredDomainProposalRecord
except ImportError:  # pragma: no cover - fallback when store schema v13 is absent
    StoredDomainProposalRecord = None  # type: ignore[assignment,misc]


class DomainExecutionRequest(BaseModel):
    """Input specification for executing a Scope B DomainPack proposal."""

    run_id: str
    domain: DomainKind
    target_resource: str
    summary: str
    payload: dict[str, Any] = Field(default_factory=dict)
    rollback_plan: str = ""
    builder_actor: str = "domain-agent-1"
    verifier_actor: str = "domain-verifier-1"
    human_approvers: list[str] = Field(default_factory=list)
    explicit_authorization_ref: str | None = None
    obligation_ids: list[str] | None = None
    stage: PolicyStage = PolicyStage.DEPLOY
    evidence_fresh: bool = True
    drift_detected: bool = False
    domain_tags: list[str] = Field(default_factory=list)
    auto_apply_when_approved: bool = True


class DomainExecutionResult(BaseModel):
    """Structured result emitted at completion of a governed Scope B execution."""

    run_id: str
    domain: DomainKind
    proposal: DomainArtifactProposal
    validation_errors: list[str]
    proof_outcomes: list[DomainProofOutcome]
    release_decision: ReleaseDecision
    dsse_envelope: DSSEEnvelope
    applied: bool
    apply_receipt: dict[str, Any] | None = None
    published_fact_id: str | None = None


def _resolve_fact_type(
    release_decision: ReleaseDecision,
    validation_errors: list[str],
    proof_outcomes: list[DomainProofOutcome],
) -> FactType:
    if release_decision.can_release:
        return FactType.VERIFIED_DELIVERY
    if validation_errors or any(not outcome.passed for outcome in proof_outcomes):
        return FactType.VERIFICATION_FAILURE
    if release_decision.outcome in {
        ReleaseOutcome.HUMAN_APPROVAL_REQUIRED,
        ReleaseOutcome.RESTRICTED_AUTHORIZATION_REQUIRED,
    }:
        return FactType.REJECTED_CHANGE
    return FactType.POLICY_DENIAL


class DomainPackExecutionRunner:
    """Orchestrates Scope B DomainPack proposals through VAD verification, policy, DSSE, and memory."""

    def __init__(
        self,
        *,
        registry: DomainPackRegistry | None = None,
        store: Any | None = None,
        memory_gateway: MemoryGateway | None = None,
        key_provider: LocalEd25519KeyProvider | None = None,
        decision_engine: ReleaseDecisionEngine | None = None,
    ) -> None:
        self.registry = (
            registry if registry is not None else create_default_domain_pack_registry()
        )
        self.store = store
        self.memory_gateway = (
            memory_gateway
            if memory_gateway is not None
            else MemoryGateway(provider=NullMemoryProvider())
        )
        self.key_provider = (
            key_provider if key_provider is not None else LocalEd25519KeyProvider()
        )
        self.decision_engine = decision_engine

    def execute(self, request: DomainExecutionRequest) -> DomainExecutionResult:
        """Execute a Scope B proposal end-to-end with fail-closed gating and DSSE attestation."""
        pack = self.registry.get(request.domain)

        proposal = pack.proposal_adapter.build_proposal(
            target_resource=request.target_resource,
            summary=request.summary,
            payload=request.payload,
            rollback_plan=request.rollback_plan,
        )

        builder = request.builder_actor.strip()
        verifier = request.verifier_actor.strip()
        if not builder or not verifier:
            raise PermissionError("builder_actor and verifier_actor must be non-empty")
        if builder == verifier:
            raise PermissionError(
                "verifier_actor must be distinct from builder_actor for independent verification"
            )

        target_obligations = (
            list(request.obligation_ids)
            if request.obligation_ids is not None
            else [f"{pack.domain.value}-default-obligation"]
        )
        if not target_obligations:
            raise ValueError("At least one obligation_id is required to execute a domain proposal")

        validation_errors = list(pack.policy_pack.validate_proposal(proposal))
        proof_outcomes: list[DomainProofOutcome] = []
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
            proof_outcomes.append(outcome)

        release_decision = evaluate_domain_proposal(
            pack,
            proposal,
            run_id=request.run_id,
            builder_actor=request.builder_actor,
            verifier_actor=request.verifier_actor,
            human_approvers=request.human_approvers,
            explicit_authorization_ref=request.explicit_authorization_ref,
            obligation_ids=request.obligation_ids,
            stage=request.stage,
            evidence_fresh=request.evidence_fresh,
            drift_detected=request.drift_detected,
            decision_engine=self.decision_engine,
        )

        attestor = DSSEAttestor(key_provider=self.key_provider, strict=True)
        review_decision = "approved" if release_decision.can_release else "rejected"
        dsse_envelope = attestor.create_review_attestation(
            commit_sha=proposal.payload_digest,
            review_decision=review_decision,
            reviewer_agent=verifier,
            proof_summary={
                "domain": request.domain.value,
                "proposal_id": proposal.proposal_id,
                "target_resource": proposal.target_resource,
                "decision_id": release_decision.decision_id,
                "outcome": release_decision.outcome.value,
                "validation_errors": validation_errors,
                "obligations": [
                    {
                        "obligation_id": item.obligation_id,
                        "passed": item.passed,
                        "isolated": item.isolated,
                        "evidence_digest": item.evidence_digest,
                    }
                    for item in proof_outcomes
                ],
            },
            eip_digest=proposal.payload_digest,
            proof_plan_digest=release_decision.evidence_digest,
            assurance_level=release_decision.assurance_achieved.value,
        )

        applied = False
        apply_receipt: dict[str, Any] | None = None
        if request.auto_apply_when_approved and release_decision.can_release:
            apply_receipt = pack.proposal_adapter.apply_proposal(
                proposal,
                approved_decision_id=release_decision.decision_id,
            )
            applied = True

        if self.store is not None:
            if hasattr(self.store, "save_release_decision"):
                self.store.save_release_decision(release_decision)
            if hasattr(self.store, "save_domain_proposal_record"):
                record_kwargs: dict[str, Any] = {
                    "proposal_id": proposal.proposal_id,
                    "run_id": request.run_id,
                    "domain": request.domain.value,
                    "target_resource": proposal.target_resource,
                    "payload_digest": proposal.payload_digest,
                    "decision_id": release_decision.decision_id,
                    "can_release": release_decision.can_release,
                    "applied": applied,
                    "proposal_payload": proposal.model_dump(mode="json"),
                    "apply_receipt": apply_receipt,
                }
                if StoredDomainProposalRecord is not None:
                    self.store.save_domain_proposal_record(
                        StoredDomainProposalRecord(**record_kwargs)
                    )
                else:
                    self.store.save_domain_proposal_record(record_kwargs)

        publisher = LearningLoopPublisher(
            self.memory_gateway,
            store=self.store if hasattr(self.store, "upsert_lesson_promotion") else None,
        )
        domain_tags = list(
            dict.fromkeys([f"domain:{request.domain.value}", *request.domain_tags])
        )
        fact = publisher.publish_run_outcome(
            run_id=request.run_id,
            eip_digest=proposal.payload_digest,
            evidence_digest=release_decision.evidence_digest,
            risk_tier=release_decision.risk_tier.value,
            fact_type=_resolve_fact_type(
                release_decision, validation_errors, proof_outcomes
            ),
            summary=(
                f"Domain {request.domain.value} run {request.run_id} "
                f"({release_decision.risk_tier.value}): {release_decision.outcome.value} "
                f"for {proposal.target_resource} - {proposal.summary}"
            ),
            domain_tags=domain_tags,
            metadata={
                "domain": request.domain.value,
                "proposal_id": proposal.proposal_id,
                "target_resource": proposal.target_resource,
                "decision_id": release_decision.decision_id,
                "outcome": release_decision.outcome.value,
                "applied": applied,
                "validation_errors": validation_errors,
            },
        )

        return DomainExecutionResult(
            run_id=request.run_id,
            domain=request.domain,
            proposal=proposal,
            validation_errors=validation_errors,
            proof_outcomes=proof_outcomes,
            release_decision=release_decision,
            dsse_envelope=dsse_envelope,
            applied=applied,
            apply_receipt=apply_receipt,
            published_fact_id=fact.fact_id,
        )
