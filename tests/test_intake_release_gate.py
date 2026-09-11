import pytest
from vad.control_plane.work_items import WorkItem, WorkItemGovernance, WorkItemStatus
from vad.policy.intake_release_gate import (
    IntakeReleaseGate,
    IntakeReleaseGateConfig,
    ReleaseGateDecision,
    ReleaseSignoff,
    evaluate_intake_release_gate,
)


def _make_work_item(
    status=WorkItemStatus.VERIFYING,
    evidence_digest="a" * 64,
    blocked_reason=None,
    high_risk=False,
    approval_required=False,
    operator_intent_ref=None,
):
    gov = None
    if high_risk or approval_required or operator_intent_ref:
        gov = WorkItemGovernance(
            effort_type="feature",
            mees_estimate=80,
            token_budget=50000,
            approval_required=True,
            high_risk=high_risk,
            operator_intent_ref=operator_intent_ref or "intent-123",
        )
    return WorkItem(
        work_item_id="wi-100",
        run_id="run-1",
        title="Implement secure token rotation",
        description="Rotates session secrets every 24 hours.",
        role="developer",
        status=status,
        evidence_digest=evidence_digest,
        blocked_reason=blocked_reason,
        governance=gov,
    )


def test_release_gate_allows_low_risk_work_with_evidence_digest():
    wi = _make_work_item(high_risk=False)
    res = evaluate_intake_release_gate(wi)
    assert res.decision == ReleaseGateDecision.ALLOWED
    assert res.can_close_remote_ticket is True
    assert res.evidence_digest == "a" * 64


def test_release_gate_denies_without_evidence_digest():
    wi = _make_work_item(evidence_digest=None)
    res = evaluate_intake_release_gate(wi)
    assert res.decision == ReleaseGateDecision.DENIED
    assert res.can_close_remote_ticket is False
    assert any("evidence digest" in r.lower() for r in res.reasons)


def test_release_gate_requires_dual_signoff_for_high_risk():
    wi = _make_work_item(high_risk=True)
    res = evaluate_intake_release_gate(wi, signoffs=[])
    assert res.decision == ReleaseGateDecision.NEEDS_APPROVAL
    assert res.can_close_remote_ticket is False
    assert any("2-person" in r.lower() for r in res.reasons)


def test_release_gate_rejects_single_actor_impersonating_dual_signoff():
    wi = _make_work_item(high_risk=True)
    signoffs = [
        ReleaseSignoff(actor="agent-smith", role="builder"),
        ReleaseSignoff(actor="agent-smith", role="guardian"),
    ]
    res = evaluate_intake_release_gate(wi, signoffs=signoffs)
    assert res.decision == ReleaseGateDecision.NEEDS_APPROVAL
    assert res.can_close_remote_ticket is False
    assert any("2 distinct actors" in r for r in res.reasons)


def test_release_gate_allows_high_risk_with_two_distinct_actors():
    wi = _make_work_item(high_risk=True)
    signoffs = [
        ReleaseSignoff(actor="agent-coder", role="verifier"),
        ReleaseSignoff(actor="human-operator", role="release_guardian"),
    ]
    res = evaluate_intake_release_gate(wi, signoffs=signoffs)
    assert res.decision == ReleaseGateDecision.ALLOWED
    assert res.can_close_remote_ticket is True


def test_release_gate_denies_when_work_item_blocked_or_tests_failed():
    wi_blocked = _make_work_item(blocked_reason="Database migration failed")
    res1 = evaluate_intake_release_gate(wi_blocked)
    assert res1.decision == ReleaseGateDecision.DENIED
    assert res1.can_close_remote_ticket is False

    wi_failed_test = _make_work_item()
    res2 = evaluate_intake_release_gate(wi_failed_test, test_results={"passed": False})
    assert res2.decision == ReleaseGateDecision.DENIED
    assert res2.can_close_remote_ticket is False
