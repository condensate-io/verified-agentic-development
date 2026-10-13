import pytest
from vad.plugins.claude_code.commands import VADCommandHandler
from vad.verify.report import VerificationReceipt
from vad.verify.runner import CostOrderedLadderRunner


def test_cost_ordered_ladder_happy_path(tmp_path):
    runner = CostOrderedLadderRunner(cwd=str(tmp_path.as_posix()))
    receipt = runner.run_ladder(commit_sha="abc1234", mees_score=85)

    assert isinstance(receipt, VerificationReceipt)
    assert receipt.passed is True
    assert len(receipt.receipt_sha) == 64
    assert len(receipt.steps) == 5

    # Check order
    step_names = [s.step_name for s in receipt.steps]
    assert step_names == ["lint", "types", "scoped_tests", "registry_integration", "model_review"]
    assert all(s.status == "PASS" for s in receipt.steps)


def test_cost_ordered_ladder_fail_fast(tmp_path):
    # Mock executor that fails on step 1 (lint)
    mock_executors = {
        "lint": lambda: (False, "", "Fatal syntax error in main.py"),
    }
    runner = CostOrderedLadderRunner(cwd=str(tmp_path.as_posix()), custom_executors=mock_executors)
    receipt = runner.run_ladder(commit_sha="deadbeef", mees_score=90)

    assert receipt.passed is False
    assert "Step 1 (lint) failed" in receipt.first_failure
    assert receipt.steps[0].status == "FAIL"

    # All subsequent steps MUST be SKIPPED
    for s in receipt.steps[1:]:
        assert s.status == "SKIPPED"

    # SHA is still stamped on failure
    assert len(receipt.receipt_sha) == 64


def test_cost_ordered_ladder_mees_model_review_failure(tmp_path):
    runner = CostOrderedLadderRunner(cwd=str(tmp_path.as_posix()))
    # MEES < 50 must fail model review step
    receipt = runner.run_ladder(commit_sha="commit-mees-low", mees_score=42)

    assert receipt.passed is False
    assert receipt.steps[4].step_name == "model_review"
    assert receipt.steps[4].status == "FAIL"
    assert "threshold 50" in receipt.steps[4].error


def test_vad_slash_command_verify(tmp_path):
    handler = VADCommandHandler(workspace_root=str(tmp_path.as_posix()))
    res = handler.handle("/vad verify")
    assert res.command == "/vad verify"
    assert res.status == "success"
    assert "receipt_sha" in res.data
    assert len(res.data["receipt_sha"]) == 64


def test_vad_slash_command_review_mees_scaling():
    handler = VADCommandHandler()

    # MEES >= 70 -> solo
    res_solo = handler.handle("/vad review --mees=85")
    assert res_solo.status == "success"
    assert res_solo.data["review_depth"] == "solo"
    assert len(res_solo.data["reviewers"]) == 1

    # MEES 50-69 -> domain
    res_domain = handler.handle("/vad review --mees=65")
    assert res_domain.status == "success"
    assert res_domain.data["review_depth"] == "domain"
    assert len(res_domain.data["reviewers"]) == 2
    assert "domain-copilot" in res_domain.data["reviewers"]

    # MEES < 50 -> adversarial
    res_adv = handler.handle("/vad review --mees=30")
    assert res_adv.status == "success"
    assert res_adv.data["review_depth"] == "adversarial"
    assert len(res_adv.data["reviewers"]) == 3
    assert "adversarial-copilot" in res_adv.data["reviewers"]
