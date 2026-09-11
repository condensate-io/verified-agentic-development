import pytest
from vad.review.dsse_attestation import DSSEAttestor
from vad.review.rebase_guard import AttestationValidity, RebaseGuard


@pytest.fixture
def rebase_fixture():
    secret = b"test-rebase-secret-key"
    attestor = DSSEAttestor()
    guard = RebaseGuard(attestor=attestor, secret_key=secret)

    commit_sha = "1111111111111111111111111111111111111111"
    envelope = attestor.create_review_attestation(
        commit_sha=commit_sha,
        review_decision="approved",
        reviewer_agent="sec-reviewer",
        proof_summary={"patch_id": "patch-abc-123", "tests": "pass"},
        secret_key=secret,
    )
    return guard, commit_sha, envelope


def test_rebase_guard_exact_match(rebase_fixture):
    guard, commit_sha, envelope = rebase_fixture
    res = guard.verify_commit_attestation(envelope, current_commit_sha=commit_sha)
    assert res.validity == AttestationValidity.VALID
    assert res.needs_re_review is False


def test_rebase_guard_stable_rebase_with_matching_patch(rebase_fixture):
    guard, orig_sha, envelope = rebase_fixture
    new_rebased_sha = "2222222222222222222222222222222222222222"

    # Same patch-id -> valid through rebase
    res = guard.verify_commit_attestation(
        envelope, current_commit_sha=new_rebased_sha, current_patch_id="patch-abc-123"
    )
    assert res.validity == AttestationValidity.VALID
    assert res.needs_re_review is False
    assert res.patch_equivalent is True


def test_rebase_guard_flags_stale_when_patch_drifts(rebase_fixture):
    guard, orig_sha, envelope = rebase_fixture
    new_rebased_sha = "3333333333333333333333333333333333333333"

    # Differing patch-id -> stale, re-review required
    res = guard.verify_commit_attestation(
        envelope, current_commit_sha=new_rebased_sha, current_patch_id="patch-changed-456"
    )
    assert res.validity == AttestationValidity.REBASE_STALE
    assert res.needs_re_review is True
    assert "Rebase invalidated attestation" in res.reason
