from __future__ import annotations

from uuid import uuid4

from pydantic import BaseModel

from vad.policy.decisions import PolicyDecision
from vad.server.db.store import ApprovalEvent, ServerStore


AUTHORIZED_APPROVAL_ROLES = {"release_guardian"}


class ApprovalRequest(BaseModel):
    run_id: str
    actor: str
    actor_role: str
    action: str = "approve_release"
    reason: str | None = None


class ApprovalResult(BaseModel):
    status_code: int
    event: ApprovalEvent


class ApprovalService:
    def __init__(self, store: ServerStore):
        self.store = store

    def record_approval(self, request: ApprovalRequest) -> ApprovalResult:
        run = self.store.load_run_evidence(request.run_id)
        decision = self._decision(request, builder=run.evidence.agents.builder)
        event = ApprovalEvent(
            approval_id=f"approval-{uuid4().hex}",
            run_id=request.run_id,
            actor=request.actor,
            action=request.action,
            decision=decision,
            evidence_digest=run.evidence_digest,
        )
        self.store.save_approval_event(event)
        return ApprovalResult(status_code=201 if decision.allow else 403, event=event)

    def _decision(self, request: ApprovalRequest, builder: str) -> PolicyDecision:
        denials = []
        if request.actor_role not in AUTHORIZED_APPROVAL_ROLES:
            denials.append("approval actor role is not authorized")
        if request.actor == builder:
            denials.append("builder may not approve own run")
        if request.action != "approve_release":
            denials.append("unsupported approval action")
        if denials:
            return PolicyDecision(allow=False, denials=denials, requires_human=True)
        return PolicyDecision(allow=True, reasons=["approval policy satisfied"])
