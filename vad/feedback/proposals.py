from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from vad.contracts.models import EIP, ProofObligation


class ProposalType(str, Enum):
    ADD_INVARIANT = "add_invariant"
    STRENGTHEN_INVARIANT = "strengthen_invariant"
    ADD_PROOF_OBLIGATION = "add_proof_obligation"
    REDUCE_AUTONOMY = "reduce_autonomy"
    RESTRICT_TOOL = "restrict_tool"
    CHANGE_MODEL_TIER = "change_model_tier"
    ADJUST_BUDGET = "adjust_budget"
    ADD_RELEASE_GATE = "add_release_gate"


class FeedbackProposal(BaseModel):
    proposal_type: ProposalType
    reason: str = Field(min_length=1)
    payload: dict[str, Any] = Field(default_factory=dict)
    approved: bool = False


def apply_approved_proposals(eip: EIP, proposals: list[FeedbackProposal]) -> EIP:
    updated = eip.model_copy(deep=True)
    for proposal in proposals:
        if not proposal.approved:
            continue
        if proposal.proposal_type in {ProposalType.ADD_INVARIANT, ProposalType.STRENGTHEN_INVARIANT}:
            _apply_invariant(updated, proposal)
        elif proposal.proposal_type == ProposalType.ADD_PROOF_OBLIGATION:
            _apply_proof_obligation(updated, proposal)
    return EIP(**updated.model_dump())


def _apply_invariant(eip: EIP, proposal: FeedbackProposal) -> None:
    category = proposal.payload.get("category", "functional")
    description = proposal.payload.get("description", proposal.reason)
    invariants = getattr(eip.invariants, category)
    if description not in invariants:
        invariants.append(description)


def _apply_proof_obligation(eip: EIP, proposal: FeedbackProposal) -> None:
    existing_ids = {obligation.id for obligation in eip.proof_obligations}
    obligation_id = proposal.payload.get("id") or _next_obligation_id(existing_ids)
    if obligation_id in existing_ids:
        return
    eip.proof_obligations.append(ProofObligation(
        id=obligation_id,
        kind=proposal.payload.get("kind", "unit"),
        description=proposal.payload.get("description", proposal.reason),
    ))


def _next_obligation_id(existing_ids: set[str]) -> str:
    index = 1
    while f"po-{index}" in existing_ids:
        index += 1
    return f"po-{index}"
