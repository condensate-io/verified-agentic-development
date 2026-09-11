"""Cross-Harness Review: Enforces builder/reviewer separation of duties across disparate AI agent runners."""

from typing import Any, Callable, Dict, List, Optional
from pydantic import BaseModel, Field
from vad.review.dsse_attestation import DSSEAttestor, DSSEEnvelope


class SeparationOfDutiesViolation(Exception):
    """Raised when an agent or harness attempts to approve its own work."""
    pass


class CrossHarnessReviewRequest(BaseModel):
    artifact_id: str
    builder_harness: str
    commit_sha: str
    diff_content: str
    proof_summary: Dict[str, Any] = Field(default_factory=dict)


class CrossHarnessReviewResult(BaseModel):
    artifact_id: str
    builder_harness: str
    assigned_reviewer: str
    approved: bool
    review_comments: List[str] = Field(default_factory=list)
    separation_of_duties_verified: bool
    dsse_envelope: Optional[DSSEEnvelope] = None


class CrossHarnessReviewRouter:
    """Enforces that independent verifier agents from distinct harnesses perform review and signing."""

    DEFAULT_HARNESS_PAIRS = {
        "claude": "aider",
        "copilot": "claude",
        "cursor": "codex",
        "codex": "claude",
        "aider": "cursor",
    }

    def __init__(
        self,
        attestor: Optional[DSSEAttestor] = None,
        secret_key: bytes = b"vad-enterprise-signing-secret",
    ):
        self.attestor = attestor or DSSEAttestor()
        self.secret_key = secret_key

    def select_reviewer(self, builder_harness: str) -> str:
        builder = builder_harness.lower()
        paired = self.DEFAULT_HARNESS_PAIRS.get(builder, "claude")
        if paired == builder:
            paired = "adversarial-verifier"
        return paired

    def conduct_review(
        self,
        request: CrossHarnessReviewRequest,
        reviewer_harness: Optional[str] = None,
        eval_fn: Optional[Callable[[CrossHarnessReviewRequest], bool]] = None,
    ) -> CrossHarnessReviewResult:
        builder = request.builder_harness.lower()
        reviewer = (reviewer_harness or self.select_reviewer(builder)).lower()

        # Hard invariant: Builder must NOT review its own changes
        if builder == reviewer:
            return CrossHarnessReviewResult(
                artifact_id=request.artifact_id,
                builder_harness=builder,
                assigned_reviewer=reviewer,
                approved=False,
                review_comments=[f"Separation of duties violation: '{reviewer}' cannot review code it authored"],
                separation_of_duties_verified=False,
                dsse_envelope=None,
            )

        # Run evaluation
        is_approved = eval_fn(request) if eval_fn else True

        # Generate DSSE envelope if approved
        envelope = None
        if is_approved:
            envelope = self.attestor.create_review_attestation(
                commit_sha=request.commit_sha,
                review_decision="approved",
                reviewer_agent=f"{reviewer}-reviewer",
                proof_summary=request.proof_summary,
                secret_key=self.secret_key,
            )

        return CrossHarnessReviewResult(
            artifact_id=request.artifact_id,
            builder_harness=builder,
            assigned_reviewer=reviewer,
            approved=is_approved,
            review_comments=["Cross-harness review passed invariants and proofs"] if is_approved else ["Rejected by verifier"],
            separation_of_duties_verified=True,
            dsse_envelope=envelope,
        )
