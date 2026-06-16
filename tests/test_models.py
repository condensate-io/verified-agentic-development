import pytest
from pydantic import ValidationError
from vad.contracts.models import EIP, Goal, Invariants, ProofObligation, RiskTier

def test_valid_eip():
    eip = EIP(
        version="1.0.0",
        name="Test",
        risk_tier=RiskTier.LOW,
        goal=Goal(description="test", success_criteria=["pass"]),
        invariants=Invariants(),
        proof_obligations=[]
    )
    assert eip.version == "1.0.0"

def test_missing_goal():
    with pytest.raises(ValidationError):
        EIP(
            version="1.0.0",
            name="Test",
            risk_tier=RiskTier.LOW,
            invariants=Invariants(),
            proof_obligations=[]
        )

def test_unknown_risk_tier():
    with pytest.raises(ValidationError):
        EIP(
            version="1.0.0",
            name="Test",
            risk_tier="unknown",
            goal=Goal(description="test", success_criteria=["pass"]),
            invariants=Invariants(),
            proof_obligations=[]
        )

def test_malformed_invariants():
    with pytest.raises(ValidationError):
        EIP(
            version="1.0.0",
            name="Test",
            risk_tier=RiskTier.LOW,
            goal=Goal(description="test", success_criteria=["pass"]),
            invariants={"security": "not_a_list"},
            proof_obligations=[]
        )

def test_invalid_proof_kinds():
    # pydantic validates types, so we can check missing fields or wrong types
    with pytest.raises(ValidationError):
        ProofObligation(
            id="1",
            kind=123,  # kind should be string
            description="desc"
        )
