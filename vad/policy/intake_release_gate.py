"""Two-person release gate and DSSE proof attestation before closing remote tickets."""
from __future__ import annotations

import re
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from vad.control_plane.work_items import WorkItem, WorkItemStatus


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ReleaseGateDecision(str, Enum):
    ALLOWED = "allowed"
    DENIED = "denied"
    NEEDS_APPROVAL = "needs_approval"


class ReleaseSignoff(BaseModel):
    actor: str = Field(min_length=1, max_length=160)
    role: str = Field(min_length=1, max_length=80)
    timestamp: datetime = Field(default_factory=_utc_now)
    signature_or_ref: Optional[str] = Field(default=None, max_length=200)


class IntakeReleaseGateResult(BaseModel):
    decision: ReleaseGateDecision
    can_close_remote_ticket: bool
    target_remote_status: str = "closed"
    reasons: List[str] = Field(default_factory=list)
    evidence_digest: Optional[str] = None
    dual_signoffs: List[ReleaseSignoff] = Field(default_factory=list)


class IntakeReleaseGateConfig(BaseModel):
    require_dual_signoff_for_high_risk: bool = True
    require_dual_signoff_all: bool = False
    require_evidence_digest: bool = True


class IntakeReleaseGate:
    """Enforces 2-Person release controls and proof attestation before closing external tickets."""

    def __init__(self, config: Optional[IntakeReleaseGateConfig] = None):
        self.config = config or IntakeReleaseGateConfig()

    def evaluate(
        self,
        work_item: WorkItem | Dict[str, Any],
        signoffs: Optional[List[ReleaseSignoff]] = None,
        test_results: Optional[Dict[str, Any]] = None,
    ) -> IntakeReleaseGateResult:
        signoffs = signoffs or []
        reasons: List[str] = []

        if isinstance(work_item, dict):
            status = work_item.get("status", WorkItemStatus.QUEUED)
            if isinstance(status, str):
                try:
                    status = WorkItemStatus(status)
                except ValueError:
                    status = WorkItemStatus.QUEUED
            evidence_digest = work_item.get("evidence_digest")
            blocked_reason = work_item.get("blocked_reason")
            governance = work_item.get("governance") or {}
            is_high_risk = bool(
                governance.get("high_risk", False)
                or governance.get("live_service_opt_in", False)
                or governance.get("approval_required", False)
            )
        else:
            status = work_item.status
            evidence_digest = work_item.evidence_digest
            blocked_reason = work_item.blocked_reason
            gov = work_item.governance
            is_high_risk = bool(gov and (gov.high_risk or gov.live_service_opt_in or gov.approval_required))

        if blocked_reason:
            return IntakeReleaseGateResult(
                decision=ReleaseGateDecision.DENIED,
                can_close_remote_ticket=False,
                reasons=[f"Work item has active blocker: {blocked_reason}"],
                evidence_digest=evidence_digest,
                dual_signoffs=signoffs,
            )

        if test_results and not test_results.get("passed", True):
            return IntakeReleaseGateResult(
                decision=ReleaseGateDecision.DENIED,
                can_close_remote_ticket=False,
                reasons=["Verification test suite failed; cannot close remote ticket"],
                evidence_digest=evidence_digest,
                dual_signoffs=signoffs,
            )

        if self.config.require_evidence_digest:
            if not evidence_digest or not re.match(r"^[a-f0-9]{64}$", str(evidence_digest)):
                reasons.append("Missing valid 64-character SHA256 evidence digest attestation")

        needs_dual = self.config.require_dual_signoff_all or (is_high_risk and self.config.require_dual_signoff_for_high_risk)
        if needs_dual:
            distinct_actors = {s.actor for s in signoffs}
            if len(distinct_actors) < 2:
                reasons.append(f"2-Person Release Guardian approval required: expected 2 distinct actors, found {len(distinct_actors)}")

        if reasons:
            decision = ReleaseGateDecision.NEEDS_APPROVAL if any("2-Person" in r for r in reasons) else ReleaseGateDecision.DENIED
            return IntakeReleaseGateResult(
                decision=decision,
                can_close_remote_ticket=False,
                reasons=reasons,
                evidence_digest=evidence_digest,
                dual_signoffs=signoffs,
            )

        return IntakeReleaseGateResult(
            decision=ReleaseGateDecision.ALLOWED,
            can_close_remote_ticket=True,
            target_remote_status="closed",
            reasons=["Release gate passed: verified attestation and required sign-offs recorded"],
            evidence_digest=evidence_digest,
            dual_signoffs=signoffs,
        )


def evaluate_intake_release_gate(
    work_item: WorkItem | Dict[str, Any],
    signoffs: Optional[List[ReleaseSignoff]] = None,
    test_results: Optional[Dict[str, Any]] = None,
    config: Optional[IntakeReleaseGateConfig] = None,
) -> IntakeReleaseGateResult:
    return IntakeReleaseGate(config).evaluate(work_item, signoffs, test_results)
