import pytest
import json
from pydantic import ValidationError
from pathlib import Path
from vad.contracts.models import (
    AutonomyTier,
    Constraints,
    EIP,
    Goal,
    Invariants,
    MemoryRequirement,
    ModelBudget,
    ProofKind,
    ProofObligation,
    ReleaseRequirements,
    RiskTier,
    TelemetryRequirements,
    ToolPermissions,
)

def create_valid_eip(**overrides):
    data = {
        "version": "1.0.0",
        "name": "Test",
        "goal": Goal(description="test", success_criteria=["pass"]),
        "non_goals": ["do not deploy"],
        "risk_tier": RiskTier.LOW,
        "autonomy_tier": AutonomyTier.ASSISTED,
        "scope_boundaries": ["tests"],
        "invariants": Invariants(),
        "constraints": Constraints(),
        "proof_obligations": [ProofObligation(id="po-1", kind=ProofKind.UNIT, description="unit proof")],
        "tool_permissions": ToolPermissions(allowed=["pytest"], denied=["network"]),
        "memory_requirements": [MemoryRequirement(scope="project", purpose="tests")],
        "model_budget": ModelBudget(max_tokens=1000, max_cost=1.0, max_loop_depth=3),
        "release_requirements": ReleaseRequirements(required=False, gates=[]),
        "telemetry_requirements": TelemetryRequirements(required=False, signals=[]),
    }
    data.update(overrides)
    return EIP(**data)

def test_valid_eip():
    eip = create_valid_eip()
    assert eip.version == "1.0.0"
    assert eip.autonomy_tier == AutonomyTier.ASSISTED

def test_missing_goal():
    with pytest.raises(ValidationError):
        create_valid_eip(goal=None)

def test_unknown_risk_tier():
    with pytest.raises(ValidationError):
        create_valid_eip(risk_tier="unknown")

def test_malformed_invariants():
    with pytest.raises(ValidationError):
        create_valid_eip(invariants={"security": "not_a_list"})

def test_invalid_proof_kinds():
    with pytest.raises(ValidationError):
        ProofObligation(
            id="1",
            kind="unit_test",
            description="desc"
        )

def test_missing_automation_fields():
    with pytest.raises(ValidationError):
        EIP(
            version="1.0.0",
            name="Test",
            goal=Goal(description="test", success_criteria=["pass"]),
            non_goals=[],
            risk_tier=RiskTier.LOW,
            autonomy_tier=AutonomyTier.ASSISTED,
            scope_boundaries=["tests"],
            invariants=Invariants(),
            constraints=Constraints(),
            proof_obligations=[],
            tool_permissions=ToolPermissions(),
            memory_requirements=[],
            model_budget=ModelBudget(max_tokens=1000, max_cost=1.0, max_loop_depth=3),
        )

def test_production_release_without_telemetry_fails():
    with pytest.raises(ValidationError, match="release requires telemetry"):
        create_valid_eip(
            release_requirements=ReleaseRequirements(required=True, strategy="gated", gates=["health"]),
            telemetry_requirements=TelemetryRequirements(required=False, signals=[]),
        )

def test_low_risk_no_release_can_opt_out_of_telemetry():
    eip = create_valid_eip(
        release_requirements=ReleaseRequirements(required=False, gates=[]),
        telemetry_requirements=TelemetryRequirements(required=False, signals=[]),
    )

    assert eip.release_requirements.required is False
    assert eip.telemetry_requirements.required is False

def test_schema_and_model_required_fields_are_aligned():
    schema_path = Path(__file__).resolve().parents[1] / "schemas" / "eip.schema.json"
    schema = json.loads(schema_path.read_text())

    assert set(schema["required"]) == set(EIP.model_fields.keys())
    assert set(schema["properties"].keys()) == set(EIP.model_fields.keys())
