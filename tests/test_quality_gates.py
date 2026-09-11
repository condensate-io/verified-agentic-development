import pytest
from vad.policy.quality_gates import QualityGateResult, QualityGateRunner


def test_quality_gate_runner_all_pass():
    runner = QualityGateRunner()
    context = {
        "coverage_pct": 87.5,
        "proof_obligations_passed": 3,
        "has_dsse_attestation": True,
        "mees_score": 85.0,
    }

    suite = runner.evaluate_all(context)
    assert suite.all_passed is True
    assert len(suite.failed_gates) == 0
    assert len(suite.gates) == 4


def test_quality_gate_runner_flags_failures():
    runner = QualityGateRunner()
    context = {
        "coverage_pct": 65.0,  # Below 80%
        "proof_obligations_passed": 0,  # Below 1
        "has_dsse_attestation": False,  # Missing
        "mees_score": 55.0,  # Below 70
    }

    suite = runner.evaluate_all(context)
    assert suite.all_passed is False
    assert "test_coverage" in suite.failed_gates
    assert "dsse_attestation" in suite.failed_gates
    assert "proof_obligations" in suite.failed_gates
    assert "mees_efficiency" in suite.failed_gates


def test_quality_gate_custom_registration():
    runner = QualityGateRunner()

    def custom_lint_gate(ctx):
        score = ctx.get("lint_score", 10.0)
        return QualityGateResult(
            gate_name="lint",
            passed=score >= 9.0,
            score=score,
            threshold=9.0,
            message="Clean lint",
        )

    runner.register_gate("lint", custom_lint_gate)
    res = runner.evaluate_all({"lint_score": 9.5}, gate_names=["lint"])
    assert res.all_passed is True
