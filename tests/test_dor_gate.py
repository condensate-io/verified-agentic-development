import pytest
from vad.contracts.models import (
    EIP,
    Goal,
    ProofObligation,
    ProofKind,
    Invariants,
    Constraints,
    ToolPermissions,
    ModelBudget,
    ReleaseRequirements,
    TelemetryRequirements,
    RiskTier,
    AutonomyTier,
)
from vad.decision.dor_gate import DoRGate, DoRStatus


def build_sample_eip(
    description="Implement secure authentication flow",
    success_criteria=None,
    scope_boundaries=None,
    proof_obligations=None,
    max_tokens=2000,
    risk_tier=RiskTier.LOW,
    security_invariants=None,
):
    if success_criteria is None:
        success_criteria = ["Auth token validated"]
    if scope_boundaries is None:
        scope_boundaries = ["auth_module"]
    if proof_obligations is None:
        proof_obligations = [ProofObligation(id="po-1", kind=ProofKind.UNIT, description="test auth")]
    if security_invariants is None:
        security_invariants = []

    return EIP(
        version="1.0.0",
        name="test-auth-eip",
        goal=Goal(description=description, success_criteria=success_criteria),
        non_goals=["No OAuth 1.0 support"],
        risk_tier=risk_tier,
        autonomy_tier=AutonomyTier.ASSISTED,
        scope_boundaries=scope_boundaries,
        invariants=Invariants(security=security_invariants, functional=["no bypass"]),
        constraints=Constraints(),
        proof_obligations=proof_obligations,
        tool_permissions=ToolPermissions(allowed=["pytest"]),
        memory_requirements=[],
        model_budget=ModelBudget(max_tokens=max_tokens, max_cost=1.5, max_loop_depth=2),
        release_requirements=ReleaseRequirements(required=False),
        telemetry_requirements=TelemetryRequirements(required=False),
    )


def test_dor_gate_admit_valid_eip():
    eip = build_sample_eip()
    gate = DoRGate()
    result = gate.evaluate(eip)
    assert result.admitted is True
    assert result.status == DoRStatus.ADMIT
    assert not result.missing_criteria


def test_dor_gate_rejects_empty_success_criteria():
    eip = build_sample_eip(success_criteria=[])
    gate = DoRGate()
    result = gate.evaluate(eip)
    assert result.admitted is False
    assert result.status == DoRStatus.REJECT
    assert any("success criterion" in m for m in result.missing_criteria)


def test_dor_gate_rejects_empty_scope_boundaries():
    eip = build_sample_eip(scope_boundaries=[])
    gate = DoRGate()
    result = gate.evaluate(eip)
    assert result.admitted is False
    assert any("Scope boundaries" in m for m in result.missing_criteria)


def test_dor_gate_high_risk_requires_security_invariant():
    eip = build_sample_eip(risk_tier=RiskTier.HIGH, security_invariants=[])
    gate = DoRGate()
    result = gate.evaluate(eip)
    assert result.admitted is False
    assert any("High-risk EIP requires at least one security invariant" in m for m in result.missing_criteria)

    # Adding security invariant allows admission
    eip_valid = build_sample_eip(risk_tier=RiskTier.HIGH, security_invariants=["Token signature must not be tampered"])
    result_valid = gate.evaluate(eip_valid)
    assert result_valid.admitted is True
