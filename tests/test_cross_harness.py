import pytest
from vad.review.cross_harness import (
    CrossHarnessReviewRequest,
    CrossHarnessReviewRouter,
)


def test_cross_harness_review_pairing():
    router = CrossHarnessReviewRouter()
    # Claude-built code is assigned to Aider
    reviewer = router.select_reviewer("claude")
    assert reviewer != "claude"
    assert reviewer == "aider"


def test_cross_harness_review_blocks_self_approval():
    router = CrossHarnessReviewRouter()
    req = CrossHarnessReviewRequest(
        artifact_id="art-1",
        builder_harness="claude",
        commit_sha="1234567890abcdef1234567890abcdef12345678",
        diff_content="+ def login(): pass",
    )

    # Forcing reviewer to be same as builder
    res = router.conduct_review(req, reviewer_harness="claude")
    assert res.approved is False
    assert res.separation_of_duties_verified is False
    assert "Separation of duties violation" in res.review_comments[0]
    assert res.dsse_envelope is None


def test_cross_harness_review_generates_dsse_attestation():
    router = CrossHarnessReviewRouter()
    req = CrossHarnessReviewRequest(
        artifact_id="art-2",
        builder_harness="claude",
        commit_sha="bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        diff_content="+ test passed",
        proof_summary={"proof_kind": "unit", "status": "pass"},
    )

    res = router.conduct_review(req)
    assert res.approved is True
    assert res.separation_of_duties_verified is True
    assert res.assigned_reviewer == "aider"
    assert res.dsse_envelope is not None
    assert len(res.dsse_envelope.signatures) > 0
