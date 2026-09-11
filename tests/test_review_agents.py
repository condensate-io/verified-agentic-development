import pytest
from vad.plugins.claude_code.review_agents import (
    ReviewAgentManager,
    ReviewAgentType,
    ReviewFinding,
    ReviewAssessment,
)
from vad.review.cross_harness import CrossHarnessReviewRouter, SeparationOfDutiesViolation


def test_review_agent_spec():
    mgr = ReviewAgentManager()
    spec = mgr.get_agent_spec(ReviewAgentType.SECURITY, harness="copilot")
    assert spec.agent_type == ReviewAgentType.SECURITY
    assert spec.harness == "copilot"
    assert "secret leaks" in spec.system_prompt


def test_launch_review_success():
    mgr = ReviewAgentManager()
    diff = "def add(a, b): return a + b"
    assessment = mgr.launch_review(
        artifact_id="art-101",
        builder_harness="claude",
        diff_content=diff,
    )
    assert isinstance(assessment, ReviewAssessment)
    assert assessment.approved is True
    assert assessment.cross_harness_result is not None
    assert assessment.cross_harness_result.separation_of_duties_verified is True


def test_launch_review_security_blocker():
    mgr = ReviewAgentManager()
    diff = "import os\nos.system('rm -rf /')"
    assessment = mgr.launch_review(
        artifact_id="art-dangerous",
        builder_harness="claude",
        diff_content=diff,
    )
    assert assessment.approved is False
    assert any(f.severity == "blocker" for f in assessment.findings)
    assert "rejected" in assessment.summary


def test_launch_review_separation_of_duties_guard():
    # Force mock router that tries to pair builder with itself
    class MockRouter(CrossHarnessReviewRouter):
        def select_reviewer(self, builder_harness: str) -> str:
            return builder_harness

    mgr = ReviewAgentManager(review_router=MockRouter())
    with pytest.raises(SeparationOfDutiesViolation):
        mgr.launch_review(
            artifact_id="art-self",
            builder_harness="claude",
            diff_content="pass",
        )
