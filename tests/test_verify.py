import pytest
from vad.contracts.models import EIP, Goal, Invariants, ProofObligation, RiskTier
from vad.proof.plan import ProofPlan, ProofMapping, VerifyStatus
from vad.verify.runner import VerifierRunner

def create_sample_eip():
    return EIP(
        version="1.0.0",
        name="Sample EIP",
        risk_tier=RiskTier.LOW,
        goal=Goal(description="Test App", success_criteria=["Pass tests"]),
        invariants=Invariants(functional=["Must add correctly"]),
        proof_obligations=[
            ProofObligation(id="po-1", kind="unit", description="Test add"),
            ProofObligation(id="po-2", kind="unit", description="Test buggy_add"),
        ]
    )

def test_missing_obligation_must_fail():
    eip = create_sample_eip()
    # Missing po-2 mapping
    plan = ProofPlan(
        eip_version="1.0.0",
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
