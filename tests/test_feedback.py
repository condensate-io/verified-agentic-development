import pytest
from pydantic import ValidationError

from vad.contracts.models import (
    AutonomyTier,
    Constraints,
    EIP,
    Goal,
    Invariants,
    ModelBudget,
    ProofObligation,
    ReleaseRequirements,
    RiskTier,
    TelemetryRequirements,
    ToolPermissions,
)
from vad.feedback.analyzer import FeedbackAnalyzer, proposals_from_release_outcome
from vad.feedback.proposals import FeedbackProposal, ProposalType, apply_approved_proposals


def test_feedback_proposal_serializes_and_validates():
    proposal = FeedbackProposal(
        proposal_type=ProposalType.ADD_PROOF_OBLIGATION,
        reason="Repeated verifier failure needs explicit coverage.",
        payload={"kind": "unit", "description": "Cover retry failure"},
    )

    payload = proposal.model_dump(mode="json")

    assert payload["proposal_type"] == "add_proof_obligation"
    assert FeedbackProposal(**payload) == proposal


def test_each_feedback_proposal_type_validates():
    for proposal_type in ProposalType:
        proposal = FeedbackProposal(
            proposal_type=proposal_type,
            reason="Observed run evidence requires control update.",
            payload={"source": "retro"},
        )
        assert proposal.proposal_type == proposal_type


def test_unsupported_feedback_proposal_type_fails():
    with pytest.raises(ValidationError):
        FeedbackProposal(
            proposal_type="rewrite_everything",
            reason="Unsupported proposal",
        )


def test_feedback_proposal_requires_reason():
    with pytest.raises(ValidationError):
        FeedbackProposal(proposal_type=ProposalType.ADD_INVARIANT, reason="")


def make_eip():
    return EIP(
        version="1.0.0",
        name="feedback-test",
        goal=Goal(description="Test feedback", success_criteria=["passes"]),
        non_goals=[],
        risk_tier=RiskTier.LOW,
        autonomy_tier=AutonomyTier.ASSISTED,
        scope_boundaries=["tests"],
        invariants=Invariants(functional=["existing invariant"]),
        constraints=Constraints(),
        proof_obligations=[ProofObligation(id="po-1", kind="unit", description="existing proof")],
        tool_permissions=ToolPermissions(allowed=["pytest"], denied=["network"]),
        memory_requirements=[],
        model_budget=ModelBudget(max_tokens=1000, max_cost=1.0, max_loop_depth=3),
        release_requirements=ReleaseRequirements(required=False, gates=[]),
        telemetry_requirements=TelemetryRequirements(required=False, signals=[]),
    )


def test_applying_approved_add_invariant_updates_eip():
    proposal = FeedbackProposal(
        proposal_type=ProposalType.ADD_INVARIANT,
        reason="Repeated failure should become invariant.",
        payload={"category": "functional", "description": "retry timeout must not recur"},
        approved=True,
    )

    updated = apply_approved_proposals(make_eip(), [proposal])

    assert "retry timeout must not recur" in updated.invariants.functional
    assert EIP(**updated.model_dump())


def test_unapproved_proposal_is_not_applied():
    proposal = FeedbackProposal(
        proposal_type=ProposalType.ADD_INVARIANT,
        reason="Not approved.",
        payload={"category": "functional", "description": "should not appear"},
        approved=False,
    )

    updated = apply_approved_proposals(make_eip(), [proposal])

    assert "should not appear" not in updated.invariants.functional


def test_applying_approved_proof_obligation_updates_eip():
    proposal = FeedbackProposal(
        proposal_type=ProposalType.ADD_PROOF_OBLIGATION,
        reason="Need regression proof.",
        payload={"kind": "unit", "description": "Cover retry timeout"},
        approved=True,
    )

    updated = apply_approved_proposals(make_eip(), [proposal])

    assert updated.proof_obligations[-1].id == "po-2"
    assert updated.proof_obligations[-1].description == "Cover retry timeout"
    assert EIP(**updated.model_dump())


def test_release_rollback_produces_release_gate_proposal():
    proposals = proposals_from_release_outcome({
        "event": "release_gate",
        "decision": "failed",
        "error": "Rollback triggered: 'health' health (0.9) is below threshold (0.95).",
    })

    assert proposals[0].proposal_type == ProposalType.ADD_RELEASE_GATE
    assert proposals[0].payload["metric"] == "health"


def test_missing_telemetry_produces_telemetry_proof_proposal():
    analyzer = FeedbackAnalyzer()

    proposals = analyzer.propose_release_updates({
        "event": "release_gate",
        "decision": "failed",
        "error": "Release candidate 'app' rejected: Missing telemetry integration.",
    })

    assert proposals[0].proposal_type == ProposalType.ADD_PROOF_OBLIGATION
    assert proposals[0].payload["kind"] == "telemetry"
