from datetime import timezone
from pathlib import Path
import shutil
from unittest.mock import MagicMock

import pytest

from vad.contracts.models import AssuranceLevel, RiskTier
from vad.policy.autonomy_matrix import (
    AllowedAutonomyAction,
    AutonomyEvaluationInput,
    AutonomyMatrixEvaluator,
    DEFAULT_AUTONOMY_MATRIX,
    PolicyStage,
)
from vad.policy.engine import PolicyEngine
from vad.release.decision import (
    ReleaseDecision,
    ReleaseDecisionEngine,
    ReleaseOutcome,
)


EIP_DIGEST = "a" * 64
EVIDENCE_DIGEST = "b" * 64
REGO_PATH = Path(__file__).resolve().parents[1] / "policies" / "vad.rego"


def _make_engine(use_opa: bool = False) -> ReleaseDecisionEngine:
    return ReleaseDecisionEngine(
        evaluator=AutonomyMatrixEvaluator(policy_path=str(REGO_PATH), use_opa=use_opa)
    )


def test_default_autonomy_matrix_rules():
    assert DEFAULT_AUTONOMY_MATRIX[RiskTier.LOW].required_assurance == AssuranceLevel.A1_BASIC
    assert DEFAULT_AUTONOMY_MATRIX[RiskTier.LOW].required_distinct_signoffs == 0
    assert DEFAULT_AUTONOMY_MATRIX[RiskTier.LOW].allowed_action == AllowedAutonomyAction.AUTONOMOUS_MERGE

    assert DEFAULT_AUTONOMY_MATRIX[RiskTier.MEDIUM].required_assurance == AssuranceLevel.A2_INDEPENDENT
    assert DEFAULT_AUTONOMY_MATRIX[RiskTier.MEDIUM].require_independent_verifier is True
    assert DEFAULT_AUTONOMY_MATRIX[RiskTier.MEDIUM].required_distinct_signoffs == 1
    assert (
        DEFAULT_AUTONOMY_MATRIX[RiskTier.MEDIUM].allowed_action
        == AllowedAutonomyAction.INDEPENDENT_VERIFICATION_REQUIRED
    )

    assert DEFAULT_AUTONOMY_MATRIX[RiskTier.HIGH].required_assurance == AssuranceLevel.A3_ATTESTED
    assert DEFAULT_AUTONOMY_MATRIX[RiskTier.HIGH].require_human_approval is True
    assert DEFAULT_AUTONOMY_MATRIX[RiskTier.HIGH].required_distinct_signoffs == 2
    assert DEFAULT_AUTONOMY_MATRIX[RiskTier.HIGH].allowed_action == AllowedAutonomyAction.HUMAN_APPROVAL_REQUIRED

    assert DEFAULT_AUTONOMY_MATRIX[RiskTier.CRITICAL].required_assurance == AssuranceLevel.A4_RESTRICTED
    assert DEFAULT_AUTONOMY_MATRIX[RiskTier.CRITICAL].require_explicit_authorization is True
    assert DEFAULT_AUTONOMY_MATRIX[RiskTier.CRITICAL].required_distinct_signoffs == 2
    assert (
        DEFAULT_AUTONOMY_MATRIX[RiskTier.CRITICAL].allowed_action
        == AllowedAutonomyAction.RESTRICTED_EXECUTION_AUTHORIZATION
    )


def test_low_risk_before_and_after_verification():
    engine = _make_engine()

    unverified = engine.decide(
        run_id="run-low-1",
        eip_digest=EIP_DIGEST,
        evidence_digest=EVIDENCE_DIGEST,
        eval_input=AutonomyEvaluationInput(
            risk_tier=RiskTier.LOW,
            stage=PolicyStage.CI_CD,
            verification_passed=False,
            assurance_achieved=AssuranceLevel.A1_BASIC,
            independent_verifier=False,
            evidence_fresh=True,
        ),
    )
    assert unverified.outcome == ReleaseOutcome.DENIED_POLICY
    assert unverified.can_release is False

    verified = engine.decide(
        run_id="run-low-2",
        eip_digest=EIP_DIGEST,
        evidence_digest=EVIDENCE_DIGEST,
        eval_input=AutonomyEvaluationInput(
            risk_tier=RiskTier.LOW,
            stage=PolicyStage.BUILD,
            verification_passed=True,
            assurance_achieved=AssuranceLevel.A1_BASIC,
            independent_verifier=False,
            evidence_fresh=True,
            builder_actor="alice",
        ),
    )
    assert verified.outcome == ReleaseOutcome.AUTONOMOUS_RELEASE
    assert verified.can_release is True
    assert verified.assurance_required == AssuranceLevel.A1_BASIC
    assert verified.required_signoffs == 0


def test_medium_risk_requires_independent_verifier():
    engine = _make_engine()

    missing_verifier = engine.decide(
        run_id="run-med-1",
        eip_digest=EIP_DIGEST,
        evidence_digest=EVIDENCE_DIGEST,
        eval_input=AutonomyEvaluationInput(
            risk_tier=RiskTier.MEDIUM,
            stage=PolicyStage.CI_CD,
            verification_passed=True,
            assurance_achieved=AssuranceLevel.A2_INDEPENDENT,
            independent_verifier=False,
            evidence_fresh=True,
            builder_actor="alice",
        ),
    )
    assert missing_verifier.outcome == ReleaseOutcome.DENIED_POLICY
    assert missing_verifier.can_release is False

    verified = engine.decide(
        run_id="run-med-2",
        eip_digest=EIP_DIGEST,
        evidence_digest=EVIDENCE_DIGEST,
        eval_input=AutonomyEvaluationInput(
            risk_tier=RiskTier.MEDIUM,
            stage=PolicyStage.DEPLOY,
            verification_passed=True,
            assurance_achieved=AssuranceLevel.A2_INDEPENDENT,
            independent_verifier=True,
            evidence_fresh=True,
            builder_actor="alice",
            verifier_actor="verifier-bot",
        ),
    )
    assert verified.outcome == ReleaseOutcome.AUTONOMOUS_RELEASE
    assert verified.can_release is True
    assert verified.required_signoffs == 1


def test_high_risk_requires_and_approves_with_human():
    engine = _make_engine()

    before_approval = engine.decide(
        run_id="run-high-1",
        eip_digest=EIP_DIGEST,
        evidence_digest=EVIDENCE_DIGEST,
        eval_input=AutonomyEvaluationInput(
            risk_tier=RiskTier.HIGH,
            stage=PolicyStage.DEPLOY,
            verification_passed=True,
            assurance_achieved=AssuranceLevel.A3_ATTESTED,
            independent_verifier=True,
            evidence_fresh=True,
            builder_actor="alice",
            verifier_actor="bob",
            human_approvers=[],
        ),
    )
    assert before_approval.outcome == ReleaseOutcome.HUMAN_APPROVAL_REQUIRED
    assert before_approval.can_release is False

    after_approval = engine.decide(
        run_id="run-high-2",
        eip_digest=EIP_DIGEST,
        evidence_digest=EVIDENCE_DIGEST,
        eval_input=AutonomyEvaluationInput(
            risk_tier=RiskTier.HIGH,
            stage=PolicyStage.DEPLOY,
            verification_passed=True,
            assurance_achieved=AssuranceLevel.A3_ATTESTED,
            independent_verifier=True,
            evidence_fresh=True,
            builder_actor="alice",
            verifier_actor="bob",
            human_approvers=["carol"],
        ),
    )
    assert after_approval.outcome == ReleaseOutcome.APPROVED_WITH_HUMAN
    assert after_approval.can_release is True
    assert after_approval.recorded_approvers == ["carol"]
    assert after_approval.required_signoffs == 2


def test_critical_risk_requires_explicit_auth_and_dual_distinct_approvers():
    engine = _make_engine()

    no_auth_no_approvers = engine.decide(
        run_id="run-crit-1",
        eip_digest=EIP_DIGEST,
        evidence_digest=EVIDENCE_DIGEST,
        eval_input=AutonomyEvaluationInput(
            risk_tier=RiskTier.CRITICAL,
            stage=PolicyStage.DEPLOY,
            verification_passed=True,
            assurance_achieved=AssuranceLevel.A4_RESTRICTED,
            independent_verifier=True,
            evidence_fresh=True,
            builder_actor="alice",
            verifier_actor="verifier-1",
        ),
    )
    assert no_auth_no_approvers.outcome == ReleaseOutcome.RESTRICTED_AUTHORIZATION_REQUIRED
    assert no_auth_no_approvers.can_release is False

    duplicate_approvers = engine.decide(
        run_id="run-crit-2",
        eip_digest=EIP_DIGEST,
        evidence_digest=EVIDENCE_DIGEST,
        eval_input=AutonomyEvaluationInput(
            risk_tier=RiskTier.CRITICAL,
            stage=PolicyStage.DEPLOY,
            verification_passed=True,
            assurance_achieved=AssuranceLevel.A4_RESTRICTED,
            independent_verifier=True,
            evidence_fresh=True,
            builder_actor="alice",
            verifier_actor="verifier-1",
            human_approvers=["guardian-1", "guardian-1"],
            explicit_authorization_ref="auth-ticket-99",
        ),
    )
    assert duplicate_approvers.outcome == ReleaseOutcome.RESTRICTED_AUTHORIZATION_REQUIRED
    assert duplicate_approvers.can_release is False

    missing_auth_ref = engine.decide(
        run_id="run-crit-3",
        eip_digest=EIP_DIGEST,
        evidence_digest=EVIDENCE_DIGEST,
        eval_input=AutonomyEvaluationInput(
            risk_tier=RiskTier.CRITICAL,
            stage=PolicyStage.DEPLOY,
            verification_passed=True,
            assurance_achieved=AssuranceLevel.A4_RESTRICTED,
            independent_verifier=True,
            evidence_fresh=True,
            builder_actor="alice",
            verifier_actor="verifier-1",
            human_approvers=["guardian-1", "guardian-2"],
            explicit_authorization_ref=None,
        ),
    )
    assert missing_auth_ref.outcome == ReleaseOutcome.RESTRICTED_AUTHORIZATION_REQUIRED
    assert missing_auth_ref.can_release is False

    authorized = engine.decide(
        run_id="run-crit-4",
        eip_digest=EIP_DIGEST,
        evidence_digest=EVIDENCE_DIGEST,
        eval_input=AutonomyEvaluationInput(
            risk_tier=RiskTier.CRITICAL,
            stage=PolicyStage.DEPLOY,
            verification_passed=True,
            assurance_achieved=AssuranceLevel.A4_RESTRICTED,
            independent_verifier=True,
            evidence_fresh=True,
            builder_actor="alice",
            verifier_actor="verifier-1",
            human_approvers=["guardian-1", "guardian-2"],
            explicit_authorization_ref="auth-ticket-99",
        ),
    )
    assert authorized.outcome == ReleaseOutcome.APPROVED_RESTRICTED
    assert authorized.can_release is True
    assert authorized.recorded_approvers == ["guardian-1", "guardian-2"]
    assert authorized.explicit_authorization_ref == "auth-ticket-99"


def test_fail_closed_stale_evidence():
    engine = _make_engine()
    decision = engine.decide(
        run_id="run-stale-1",
        eip_digest=EIP_DIGEST,
        evidence_digest=EVIDENCE_DIGEST,
        eval_input=AutonomyEvaluationInput(
            risk_tier=RiskTier.LOW,
            stage=PolicyStage.CI_CD,
            verification_passed=True,
            assurance_achieved=AssuranceLevel.A4_RESTRICTED,
            independent_verifier=True,
            evidence_fresh=False,
            freshness_reasons=["HEAD commit mismatch"],
        ),
    )
    assert decision.outcome == ReleaseOutcome.BLOCKED_STALE_EVIDENCE
    assert decision.can_release is False
    assert "HEAD commit mismatch" in decision.reasons


@pytest.mark.parametrize(
    ("risk_tier", "achieved"),
    [
        (RiskTier.MEDIUM, AssuranceLevel.A1_BASIC),
        (RiskTier.HIGH, AssuranceLevel.A2_INDEPENDENT),
        (RiskTier.CRITICAL, AssuranceLevel.A3_ATTESTED),
    ],
)
def test_fail_closed_insufficient_assurance(risk_tier: RiskTier, achieved: AssuranceLevel):
    engine = _make_engine()
    decision = engine.decide(
        run_id="run-assur-1",
        eip_digest=EIP_DIGEST,
        evidence_digest=EVIDENCE_DIGEST,
        eval_input=AutonomyEvaluationInput(
            risk_tier=risk_tier,
            stage=PolicyStage.CI_CD,
            verification_passed=True,
            assurance_achieved=achieved,
            independent_verifier=True,
            evidence_fresh=True,
            human_approvers=["guardian-1", "guardian-2"],
            explicit_authorization_ref="auth-1",
        ),
    )
    assert decision.outcome == ReleaseOutcome.BLOCKED_INSUFFICIENT_ASSURANCE
    assert decision.can_release is False


@pytest.mark.parametrize("stage", [PolicyStage.DRIFT, PolicyStage.RUNTIME])
def test_fail_closed_drift_detected(stage: PolicyStage):
    engine = _make_engine()
    decision = engine.decide(
        run_id="run-drift-1",
        eip_digest=EIP_DIGEST,
        evidence_digest=EVIDENCE_DIGEST,
        eval_input=AutonomyEvaluationInput(
            risk_tier=RiskTier.LOW,
            stage=stage,
            verification_passed=True,
            assurance_achieved=AssuranceLevel.A1_BASIC,
            independent_verifier=True,
            evidence_fresh=True,
            drift_detected=True,
        ),
    )
    assert decision.outcome == ReleaseOutcome.BLOCKED_DRIFT
    assert decision.can_release is False


def test_separation_of_duties_denies_builder_self_approval_and_self_verify():
    engine = _make_engine()

    self_approved = engine.decide(
        run_id="run-sod-1",
        eip_digest=EIP_DIGEST,
        evidence_digest=EVIDENCE_DIGEST,
        eval_input=AutonomyEvaluationInput(
            risk_tier=RiskTier.HIGH,
            stage=PolicyStage.DEPLOY,
            verification_passed=True,
            assurance_achieved=AssuranceLevel.A3_ATTESTED,
            independent_verifier=True,
            evidence_fresh=True,
            builder_actor="alice",
            verifier_actor="bob",
            human_approvers=["alice"],
        ),
    )
    assert self_approved.outcome == ReleaseOutcome.DENIED_POLICY
    assert self_approved.can_release is False

    self_verified = engine.decide(
        run_id="run-sod-2",
        eip_digest=EIP_DIGEST,
        evidence_digest=EVIDENCE_DIGEST,
        eval_input=AutonomyEvaluationInput(
            risk_tier=RiskTier.MEDIUM,
            stage=PolicyStage.CI_CD,
            verification_passed=True,
            assurance_achieved=AssuranceLevel.A2_INDEPENDENT,
            independent_verifier=True,
            evidence_fresh=True,
            builder_actor="alice",
            verifier_actor="alice",
        ),
    )
    assert self_verified.outcome == ReleaseOutcome.DENIED_POLICY
    assert self_verified.can_release is False


def test_release_decision_replayable_serialization():
    engine = _make_engine()
    decision = engine.decide(
        run_id="run-replay-1",
        eip_digest=EIP_DIGEST,
        evidence_digest=EVIDENCE_DIGEST,
        eval_input=AutonomyEvaluationInput(
            risk_tier=RiskTier.HIGH,
            stage=PolicyStage.DEPLOY,
            verification_passed=True,
            assurance_achieved=AssuranceLevel.A3_ATTESTED,
            independent_verifier=True,
            evidence_fresh=True,
            builder_actor="alice",
            verifier_actor="bob",
            human_approvers=["guardian-1"],
        ),
    )
    assert decision.decision_id.startswith("dec-")
    assert decision.decided_at.tzinfo == timezone.utc
    replayed = ReleaseDecision.model_validate_json(decision.model_dump_json())
    assert replayed == decision


def test_vad_rego_contains_additive_autonomy_matrix_and_preserves_existing_rules():
    rego_text = REGO_PATH.read_text(encoding="utf-8")
    for expected in (
        "deny_builder_self_approval",
        "deny_deployment_apply_without_approval",
        "deny_stale_evidence",
        "deny_drift_detected",
        "deny_insufficient_assurance",
        "allow_autonomous_release",
        "require_human_approval",
        "require_restricted_authorization",
    ):
        assert expected in rego_text


def test_opa_cross_check_fail_closed_with_mocked_engine(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(shutil, "which", lambda name: "opa" if name == "opa" else None)
    mock_policy_engine = MagicMock(spec=PolicyEngine)
    mock_policy_engine.evaluate.side_effect = [
        True,   # deny_stale_evidence
        False,  # deny_drift_detected
        False,  # deny_insufficient_assurance
        False,  # allow_autonomous_release
        False,  # require_human_approval
        False,  # require_restricted_authorization
    ]
    evaluator = AutonomyMatrixEvaluator(
        policy_path=str(REGO_PATH),
        use_opa=True,
        policy_engine=mock_policy_engine,
    )
    result = evaluator.evaluate(
        AutonomyEvaluationInput(
            risk_tier=RiskTier.LOW,
            stage=PolicyStage.CI_CD,
            verification_passed=True,
            assurance_achieved=AssuranceLevel.A1_BASIC,
            independent_verifier=False,
            evidence_fresh=True,
        )
    )
    assert result.opa_checked is True
    assert result.opa_consistent is False
    assert result.allowed is False
    assert result.action == AllowedAutonomyAction.DENY


@pytest.mark.skipif(shutil.which("opa") is None, reason="opa CLI binary not installed on host")
def test_opa_rego_autonomy_matrix_live_consistency():
    engine = PolicyEngine()
    path = str(REGO_PATH)

    low_ok = {
        "action": "evaluate_release_autonomy",
        "risk_tier": "low",
        "stage": "ci_cd",
        "verification_passed": True,
        "assurance_achieved": "A1_BASIC",
        "independent_verifier": False,
        "evidence_fresh": True,
        "drift_detected": False,
    }
    assert engine.evaluate(path, "data.vad.policy.allow_autonomous_release", low_ok) is True
    assert engine.evaluate(path, "data.vad.policy.deny_stale_evidence", low_ok) is False

    stale = {**low_ok, "evidence_fresh": False}
    assert engine.evaluate(path, "data.vad.policy.deny_stale_evidence", stale) is True
    assert engine.evaluate(path, "data.vad.policy.allow_autonomous_release", stale) is False

    drift = {**low_ok, "stage": "drift", "drift_detected": True}
    assert engine.evaluate(path, "data.vad.policy.deny_drift_detected", drift) is True
    assert engine.evaluate(path, "data.vad.policy.allow_autonomous_release", drift) is False

    med_no_ver = {**low_ok, "risk_tier": "medium", "assurance_achieved": "A2_INDEPENDENT"}
    assert engine.evaluate(path, "data.vad.policy.allow_autonomous_release", med_no_ver) is False
    med_ok = {**med_no_ver, "independent_verifier": True}
    assert engine.evaluate(path, "data.vad.policy.allow_autonomous_release", med_ok) is True

    high_ok = {**med_ok, "risk_tier": "high", "assurance_achieved": "A3_ATTESTED"}
    assert engine.evaluate(path, "data.vad.policy.require_human_approval", high_ok) is True
    assert engine.evaluate(path, "data.vad.policy.allow_autonomous_release", high_ok) is False

    high_low_assur = {**high_ok, "assurance_achieved": "A2_INDEPENDENT"}
    assert engine.evaluate(path, "data.vad.policy.deny_insufficient_assurance", high_low_assur) is True
    assert engine.evaluate(path, "data.vad.policy.require_human_approval", high_low_assur) is False

    crit_ok = {**med_ok, "risk_tier": "critical", "assurance_achieved": "A4_RESTRICTED"}
    assert engine.evaluate(path, "data.vad.policy.require_restricted_authorization", crit_ok) is True
    assert engine.evaluate(path, "data.vad.policy.allow_autonomous_release", crit_ok) is False

    # Confirm Python evaluator with use_opa=True matches live OPA
    evaluator = AutonomyMatrixEvaluator(policy_path=path, use_opa=True)
    res = evaluator.evaluate(
        AutonomyEvaluationInput(
            risk_tier=RiskTier.MEDIUM,
            stage=PolicyStage.CI_CD,
            verification_passed=True,
            assurance_achieved=AssuranceLevel.A2_INDEPENDENT,
            independent_verifier=True,
            evidence_fresh=True,
        )
    )
    assert res.opa_checked is True
    assert res.opa_consistent is True
    assert res.allowed is True
