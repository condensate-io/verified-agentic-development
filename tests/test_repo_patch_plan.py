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
from vad.repo.patch_plan import build_patch_plan
from vad.repo.proof_discovery import DiscoveredProofCommand


def make_eip():
    return EIP(
        version="1.0.0",
        name="repo-patch",
        goal=Goal(description="Patch repo", success_criteria=["tests pass"]),
        non_goals=[],
        risk_tier=RiskTier.LOW,
        autonomy_tier=AutonomyTier.BOUNDED,
        scope_boundaries=["src", "tests"],
        invariants=Invariants(),
        constraints=Constraints(),
        proof_obligations=[ProofObligation(id="po-1", kind="unit", description="unit tests")],
        tool_permissions=ToolPermissions(allowed=["pytest"], denied=["network"]),
        memory_requirements=[],
        model_budget=ModelBudget(max_tokens=1000, max_cost=1.0, max_loop_depth=3),
        release_requirements=ReleaseRequirements(required=False, gates=[]),
        telemetry_requirements=TelemetryRequirements(required=False, signals=[]),
    )


def test_patch_plan_maps_eip_scope_to_allowed_files():
    proof = [DiscoveredProofCommand(ecosystem="python", command=["pytest"], reason="tests")]

    plan = build_patch_plan(make_eip(), ["src/app.py", "tests/test_app.py"], proof, mees_estimate=88)

    assert plan.scope_boundaries == ["src", "tests"]
    assert plan.allowed_files == ["src/app.py", "tests/test_app.py"]
    assert plan.mees_estimate == 88
    assert plan.proof_commands[0].command == ["pytest"]


def test_patch_plan_rejects_out_of_scope_write():
    with pytest.raises(ValidationError, match="outside EIP scope"):
        build_patch_plan(make_eip(), ["README.md"], [], mees_estimate=90)


def test_patch_plan_rejects_path_traversal():
    with pytest.raises(ValueError, match="unsafe repository path"):
        build_patch_plan(make_eip(), ["../outside.py"], [], mees_estimate=90)


def test_patch_plan_records_blocker_when_no_proofs_available():
    plan = build_patch_plan(make_eip(), ["src/app.py"], [], mees_estimate=90)

    assert plan.blocker == "no proof commands available for patch plan"
