"""Push Confirmation Controller: Validates DSSE attestation before allowing human-confirmed git push."""

from datetime import datetime, timezone
from typing import Callable, Optional
from pydantic import BaseModel
from vad.orchestrator.staged_commit import StagedCommitManager
from vad.review.dsse_attestation import DSSEAttestor


class PushConfirmationResult(BaseModel):
    staged_id: str
    task_id: str
    pushed: bool
    reason: str
    commit_sha: str
    confirmed_by: str
    pushed_at: Optional[datetime] = None


class PushConfirmationController:
    """Safely transitions a staged commit to remote push upon operator confirmation and valid DSSE attestation."""

    def __init__(
        self,
        staged_manager: StagedCommitManager,
        attestor: Optional[DSSEAttestor] = None,
        git_push_fn: Optional[Callable[[str, str], bool]] = None,
        secret_key: bytes = b"vad-enterprise-signing-secret",
    ):
        self.staged_manager = staged_manager
        self.attestor = attestor or DSSEAttestor()
        self.git_push_fn = git_push_fn or (lambda branch, sha: True)
        self.secret_key = secret_key

    def confirm_and_push(self, staged_id: str, operator_id: str) -> PushConfirmationResult:
        staged = self.staged_manager.get_staged_item(staged_id)
        if not staged:
            return PushConfirmationResult(
                staged_id=staged_id,
                task_id="unknown",
                pushed=False,
                reason=f"Staged item '{staged_id}' not found",
                commit_sha="",
                confirmed_by=operator_id,
            )

        if staged.confirmed:
            return PushConfirmationResult(
                staged_id=staged_id,
                task_id=staged.task_id,
                pushed=False,
                reason=f"Staged item '{staged_id}' has already been confirmed and pushed",
                commit_sha=staged.commit_sha,
                confirmed_by=operator_id,
            )

        # 1. Hard invariant: DSSE attestation MUST be present
        if not staged.dsse_envelope:
            return PushConfirmationResult(
                staged_id=staged_id,
                task_id=staged.task_id,
                pushed=False,
                reason="Push rejected: missing mandatory DSSE review attestation",
                commit_sha=staged.commit_sha,
                confirmed_by=operator_id,
            )

        # 2. Hard invariant: DSSE attestation MUST be valid and approved for this commit
        is_verified = self.attestor.verify_review_attestation(
            staged.dsse_envelope,
            expected_commit_sha=staged.commit_sha,
            secret_key=self.secret_key,
        )
        if not is_verified:
            return PushConfirmationResult(
                staged_id=staged_id,
                task_id=staged.task_id,
                pushed=False,
                reason="Push rejected: DSSE review attestation signature invalid or not approved",
                commit_sha=staged.commit_sha,
                confirmed_by=operator_id,
            )

        # 3. Perform git push
        push_success = False
        try:
            push_success = self.git_push_fn(staged.branch, staged.commit_sha)
        except Exception as e:
            return PushConfirmationResult(
                staged_id=staged_id,
                task_id=staged.task_id,
                pushed=False,
                reason=f"Git push failed: {str(e)}",
                commit_sha=staged.commit_sha,
                confirmed_by=operator_id,
            )

        if not push_success:
            return PushConfirmationResult(
                staged_id=staged_id,
                task_id=staged.task_id,
                pushed=False,
                reason="Git push transport failed",
                commit_sha=staged.commit_sha,
                confirmed_by=operator_id,
            )

        now = datetime.now(timezone.utc)
        staged.confirmed = True
        staged.confirmed_by = operator_id
        staged.pushed_at = now
        self.staged_manager._save()

        # Mark task as DONE in DAG
        self.staged_manager.graph.complete(staged.task_id)

        return PushConfirmationResult(
            staged_id=staged_id,
            task_id=staged.task_id,
            pushed=True,
            reason="Successfully confirmed and pushed to remote with valid DSSE attestation",
            commit_sha=staged.commit_sha,
            confirmed_by=operator_id,
            pushed_at=now,
        )
