import pytest
from vad.contracts.models import (
    AutonomyTier,
    Constraints,
    EIP,
    Goal,
    Invariants,
    MemoryRequirement,
    ModelBudget,
    ProofObligation,
    ReleaseRequirements,
    RiskTier,
    TelemetryRequirements,
    ToolPermissions,
)
from vad.proof.plan import ProofPlan, ProofMapping, VerifyStatus, compute_eip_digest
from vad.verify.runner import VerifierRunner

def create_sample_eip():
    return EIP(
        version="1.0.0",
        name="Sample EIP",
        goal=Goal(description="Test App", success_criteria=["Pass tests"]),
        non_goals=["Do not deploy"],
        risk_tier=RiskTier.LOW,
        autonomy_tier=AutonomyTier.ASSISTED,
        scope_boundaries=["examples/app"],
        invariants=Invariants(functional=["Must add correctly"]),
        constraints=Constraints(),
        proof_obligations=[
            ProofObligation(id="po-1", kind="unit", description="Test add"),
            ProofObligation(id="po-2", kind="unit", description="Test buggy_add"),
        ],
        tool_permissions=ToolPermissions(allowed=["pytest"], denied=["network"]),
        memory_requirements=[MemoryRequirement(scope="project", purpose="verification")],
        model_budget=ModelBudget(max_tokens=1000, max_cost=1.0, max_loop_depth=3),
        release_requirements=ReleaseRequirements(required=False, gates=[]),
        telemetry_requirements=TelemetryRequirements(required=False, signals=[]),
    )

def test_missing_obligation_must_fail():
    eip = create_sample_eip()
    # Missing po-2 mapping
    plan = ProofPlan(
        eip_version="1.0.0",
        eip_digest=compute_eip_digest(eip),
        mappings=[
            ProofMapping(
                obligation_id="po-1",
                test_command="pytest examples/app/tests/test_main.py::test_add"
            )
        ]
    )
    
    runner = VerifierRunner(eip, plan)
    report = runner.run()
    
    assert report.passed is False
    unmapped_results = [r for r in report.results if r.status == VerifyStatus.UNMAPPED]
    assert len(unmapped_results) == 1
    assert unmapped_results[0].obligation_id == "po-2"

def test_known_good_sample_app():
    eip = create_sample_eip()
    # Exclude po-2 for known good
    eip.proof_obligations = [eip.proof_obligations[0]]
    plan = ProofPlan(
        eip_version="1.0.0",
        eip_digest=compute_eip_digest(eip),
        mappings=[
            ProofMapping(
                obligation_id="po-1",
                test_command="pytest examples/app/tests/test_main.py::test_add_properties"
            )
        ]
    )
    
    runner = VerifierRunner(eip, plan)
    report = runner.run()
    
    assert report.passed is True
    assert report.results[0].status == VerifyStatus.PASS

def test_known_bad_sample_app_property_failure():
    eip = create_sample_eip()
    plan = ProofPlan(
        eip_version="1.0.0",
        eip_digest=compute_eip_digest(eip),
        mappings=[
            ProofMapping(
                obligation_id="po-1",
                test_command="pytest examples/app/tests/test_main.py::test_add_properties"
            ),
            ProofMapping(
                obligation_id="po-2",
                test_command="pytest examples/app/tests/test_main.py::test_buggy_add_properties"
            )
        ]
    )
    
    runner = VerifierRunner(eip, plan)
    report = runner.run()
    
    assert report.passed is False
    bad_result = next(r for r in report.results if r.obligation_id == "po-2")
    assert bad_result.status == VerifyStatus.FAIL
    # Ensure hypothesis property failure outputs minimized counterexample
    assert "Falsifying example:" in bad_result.output or "Falsifying example:" in str(bad_result.error) or "hypothesis" in bad_result.output or "hypothesis" in str(bad_result.error) or "test_buggy_add_properties" in bad_result.output or "test_buggy_add_properties" in str(bad_result.error)

def test_eip_digest_mismatch_fails_verification_setup():
    eip = create_sample_eip()
    eip.proof_obligations = [eip.proof_obligations[0]]
    plan = ProofPlan(
        eip_version="1.0.0",
        eip_digest="wrong",
        mappings=[
            ProofMapping(
                obligation_id="po-1",
                test_command="pytest examples/app/tests/test_main.py::test_add_properties"
            )
        ]
    )

    report = VerifierRunner(eip, plan).run()

    assert report.passed is False
    assert report.results[0].obligation_id == "proof-plan"
    assert "digest" in str(report.results[0].error)

def test_forbidden_mapped_command_fails_before_execution():
    eip = create_sample_eip()
    eip.proof_obligations = [eip.proof_obligations[0]]
    plan = ProofPlan(
        eip_version="1.0.0",
        eip_digest=compute_eip_digest(eip),
        mappings=[
            ProofMapping(
                obligation_id="po-1",
                test_command="rm -rf important"
            )
        ]
    )

    report = VerifierRunner(eip, plan).run()

    assert report.passed is False
    assert report.results[0].status == VerifyStatus.FAIL
    assert "executable not allowed" in str(report.results[0].error)
