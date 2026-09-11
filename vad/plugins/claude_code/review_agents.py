"""Claude Code Review Agents: Orchestrates independent reviewer agents for on-demand cross-harness review."""

from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from vad.review.cross_harness import (
    CrossHarnessReviewRequest,
    CrossHarnessReviewResult,
    CrossHarnessReviewRouter,
    SeparationOfDutiesViolation,
)


class ReviewAgentType(str, Enum):
    SECURITY = "security"
    INVARIANTS = "invariants"
    ARCHITECTURE = "architecture"
    CROSS_HARNESS = "cross_harness"


class ReviewAgentSpec(BaseModel):
    name: str
    agent_type: ReviewAgentType
    harness: str
    system_prompt: str
    strict: bool = True


class ReviewFinding(BaseModel):
    reviewer: str
    category: str
    severity: str  # "info" | "warning" | "blocker"
    description: str
    file_path: Optional[str] = None
    line_number: Optional[int] = None


class ReviewAssessment(BaseModel):
    artifact_id: str
    approved: bool
    reviewers_involved: List[str]
    findings: List[ReviewFinding] = Field(default_factory=list)
    cross_harness_result: Optional[CrossHarnessReviewResult] = None
    summary: str


class ReviewAgentManager:
    """Manages spawning and aggregating results from independent review agents."""

    def __init__(self, review_router: Optional[CrossHarnessReviewRouter] = None):
        self.router = review_router or CrossHarnessReviewRouter()

    def get_agent_spec(self, agent_type: ReviewAgentType, harness: str = "copilot") -> ReviewAgentSpec:
        prompts = {
            ReviewAgentType.SECURITY: "Audit code diff for secret leaks, injection flaws, and authorization bypasses.",
            ReviewAgentType.INVARIANTS: "Verify that all invariant clauses declared in the EIP remain strictly preserved.",
            ReviewAgentType.ARCHITECTURE: "Assess separation of concerns, module boundaries, and architectural patterns.",
            ReviewAgentType.CROSS_HARNESS: "Perform independent verification adhering strictly to separation of duties.",
        }
        return ReviewAgentSpec(
            name=f"{agent_type.value}-reviewer",
            agent_type=agent_type,
            harness=harness,
            system_prompt=prompts.get(agent_type, "Review proposed code changes."),
            strict=True,
        )

    def launch_review(
        self,
        artifact_id: str,
        builder_harness: str,
        diff_content: str,
        commit_sha: str = "HEAD",
        review_types: Optional[List[ReviewAgentType]] = None,
        custom_findings: Optional[List[ReviewFinding]] = None,
    ) -> ReviewAssessment:
        if review_types is None:
            review_types = [ReviewAgentType.CROSS_HARNESS, ReviewAgentType.SECURITY]

        assigned_reviewer_harness = self.router.select_reviewer(builder_harness)
        if assigned_reviewer_harness.lower() == builder_harness.lower():
            raise SeparationOfDutiesViolation(
                f"Reviewer harness '{assigned_reviewer_harness}' cannot match builder '{builder_harness}'."
            )

        # Conduct cross harness review
        req = CrossHarnessReviewRequest(
            artifact_id=artifact_id,
            builder_harness=builder_harness,
            commit_sha=commit_sha,
            diff_content=diff_content,
        )
        cross_res = self.router.conduct_review(req, reviewer_harness=assigned_reviewer_harness)

        findings: List[ReviewFinding] = list(custom_findings or [])
        # If diff contains dangerous operations, register a security finding
        if "os.system(" in diff_content or "eval(" in diff_content:
            findings.append(
                ReviewFinding(
                    reviewer=f"security-{assigned_reviewer_harness}",
                    category="injection_risk",
                    severity="blocker",
                    description="Potentially hazardous function call detected (os.system/eval).",
                )
            )

        blockers = [f for f in findings if f.severity == "blocker"]
        approved = cross_res.approved and len(blockers) == 0

        reviewers = [f"{rt.value}-{assigned_reviewer_harness}" for rt in review_types]

        return ReviewAssessment(
            artifact_id=artifact_id,
            approved=approved,
            reviewers_involved=reviewers,
            findings=findings,
            cross_harness_result=cross_res,
            summary="Review approved with DSSE attestation." if approved else f"Review rejected due to {len(blockers)} blocker(s).",
        )
