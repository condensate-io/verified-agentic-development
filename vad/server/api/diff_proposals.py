from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from vad.policy.decisions import PolicyDecision
from vad.repo.diff_workflow import DiffApplyRecord, DiffProposal, apply_approved_diff_proposal, create_diff_proposal
from vad.server.db.store import ServerStore


@dataclass(frozen=True)
class DiffProposalCreateResult:
    status_code: int
    proposal: DiffProposal | None
    decision: PolicyDecision


@dataclass(frozen=True)
class DiffProposalApplyResult:
    status_code: int
    proposal: DiffProposal | None
    apply_record: DiffApplyRecord | None
    decision: PolicyDecision


class DiffProposalService:
    def __init__(self, store: ServerStore):
        self.store = store

    def create(self, body: dict) -> DiffProposalCreateResult:
        try:
            proposal = create_diff_proposal(
                run_id=body["run_id"],
                task_id=body["task_id"],
                submitted_by=body.get("submitted_by", body.get("actor", "builder")),
                role=body.get("role", "builder"),
                patch_text=body["patch_text"],
                changed_files=body["changed_files"],
                summary=body["summary"],
            )
        except (KeyError, ValidationError, ValueError) as exc:
            return DiffProposalCreateResult(
                status_code=400,
                proposal=None,
                decision=PolicyDecision(
                    allow=False,
                    denials=[str(exc)],
                    requires_human=True,
                ),
            )
        saved = self.store.save_diff_proposal(proposal)
        return DiffProposalCreateResult(
            status_code=201,
            proposal=saved,
            decision=PolicyDecision(allow=True, reasons=["diff proposal persisted"]),
        )

    def list(self, *, run_id: str | None = None, task_id: str | None = None) -> list[DiffProposal]:
        return self.store.list_diff_proposals(run_id=run_id, task_id=task_id)

    def read(self, proposal_id: str) -> DiffProposal:
        return self.store.load_diff_proposal(proposal_id)

    def apply(
        self,
        proposal_id: str,
        *,
        workspace_root: str | Path,
        verifier_decision: PolicyDecision,
        release_guardian_decision: PolicyDecision,
    ) -> DiffProposalApplyResult:
        try:
            proposal = self.store.load_diff_proposal(proposal_id)
        except KeyError:
            return DiffProposalApplyResult(
                status_code=404,
                proposal=None,
                apply_record=None,
                decision=PolicyDecision(
                    allow=False,
                    denials=[f"diff proposal {proposal_id} not found"],
                    requires_human=True,
                ),
            )
        record = apply_approved_diff_proposal(
            workspace_root,
            proposal,
            verifier_decision=verifier_decision,
            release_guardian_decision=release_guardian_decision,
        )
        saved_record = self.store.save_diff_apply_record(record)
        status = "applied" if record.applied else "denied"
        updated = self.store.update_diff_proposal_status(proposal.proposal_id, status=status)
        return DiffProposalApplyResult(
            status_code=200 if record.applied else 409,
            proposal=updated,
            apply_record=saved_record,
            decision=PolicyDecision(
                allow=record.applied,
                reasons=["diff proposal applied"] if record.applied else [],
                denials=[record.blocker] if record.blocker else [],
                requires_human=not record.applied,
            ),
        )
