import pytest
from vad.decision.calibration import CalibrationExemplar, ReviewAgentCalibrator


@pytest.fixture
def calibrator():
    cal = ReviewAgentCalibrator()
    cal.add_exemplar(
        CalibrationExemplar(
            id="ex-1",
            decision_context="Release candidate lacks automated proof of performance SLO under load",
            ground_truth_outcome="reject",
            rationale="Proof is mandatory; unproven performance constraints cannot be promoted.",
            principles_invoked=["Proof is mandatory"],
            weight=1.0,
        )
    )
    cal.add_exemplar(
        CalibrationExemplar(
            id="ex-2",
            decision_context="Builder agent requests self-approval on high-risk database migration",
            ground_truth_outcome="reject",
            rationale="Separation of duties requires an independent reviewer.",
            principles_invoked=["Separation of duties"],
            weight=1.0,
        )
    )
    return cal


def test_calibrator_matches_correct_outcome(calibrator):
    report = calibrator.calibrate(
        decision_context="Release candidate lacks automated proof of performance",
        candidate_outcome="reject",
        candidate_rationale="Rejected because proof is mandatory and performance test is absent.",
    )
    assert report.is_calibrated is True
    assert report.calibration_score >= 0.75
    assert report.alignment_delta <= 0.25


def test_calibrator_flags_uncalibrated_deviation(calibrator):
    report = calibrator.calibrate(
        decision_context="Builder agent requests self-approval on high-risk migration",
        candidate_outcome="approve",
        candidate_rationale="Looks good, developer trusts their own change.",
    )
    assert report.is_calibrated is False
    assert report.calibration_score < 0.5
    assert any("Separation of duties" in r for r in report.recommendations)


def test_calibrator_yaml_loading(tmp_path):
    yaml_file = tmp_path / "calibration.yaml"
    yaml_file.write_text(
        """
principles:
  - "Rule 1: Always verify"
exemplars:
  - id: "ex-yaml-1"
    decision_context: "Missing test evidence"
    ground_truth_outcome: "block"
    rationale: "Must have evidence"
    principles_invoked: ["Rule 1: Always verify"]
"""
    )
    cal = ReviewAgentCalibrator()
    cal.load_from_yaml(yaml_file.read_text())
    assert len(cal.exemplars) == 1
    assert cal.exemplars[0].id == "ex-yaml-1"
