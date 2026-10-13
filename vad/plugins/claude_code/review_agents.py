"""Claude Code Review Agents: Orchestrates independent reviewer agents for on-demand cross-harness review."""

from enum import Enum
from typing import Any, Dict, List, Optional, Tuple
from pydantic import BaseModel, Field

from vad.agents.lifecycle_prompts import (
    ADVERSARIAL_VERIFIER_PROMPT,
    ADVERSARIAL_VERIFIER_TEMPLATE,
)
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
    DOMAIN = "domain"
    ADVERSARIAL = "adversarial"


class ReviewDepth(str, Enum):
    SOLO = "solo"
    DOMAIN = "domain"
    ADVERSARIAL = "adversarial"


class ReviewAgentSpec(BaseModel):
    name: str
    agent_type: ReviewAgentType
    harness: str
    system_prompt: str
    strict: bool = True
    template: Optional[str] = None


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
    review_depth: Optional[ReviewDepth] = None
    mees_score: Optional[int] = None


def select_review_depth_from_mees(mees: int) -> Tuple[ReviewDepth, List[ReviewAgentType]]:
    """Determines review depth and reviewer agent roster based on MEES score.

    - MEES >= 70: Solo review (standard cross-harness reviewer)
    - 50 <= MEES < 70: Adds domain agent (domain-specialist reviewer)
    - MEES < 50: Adds domain agent plus adversarial verifier
    """
    if mees >= 70:
        return ReviewDepth.SOLO, [ReviewAgentType.CROSS_HARNESS]
    elif mees >= 50:
        return ReviewDepth.DOMAIN, [ReviewAgentType.CROSS_HARNESS, ReviewAgentType.DOMAIN]
    else:
        return ReviewDepth.ADVERSARIAL, [
            ReviewAgentType.CROSS_HARNESS,
            ReviewAgentType.DOMAIN,
            ReviewAgentType.ADVERSARIAL,
        ]


class ReviewAgentManager:
    """Manages spawning and aggregating results from independent review agents."""

    def __init__(self, review_router: Optional[CrossHarnessReviewRouter] = None):
        self.router = review_router or CrossHarnessReviewRouter()

    def get_agent_spec(self, agent_type: ReviewAgentType, harness: str = "copilot") -> ReviewAgentSpec:
        prompts = {
            ReviewAgentType.SECURITY: (
                "# Role: Security Reviewer\n\n"
                "You are an independent security auditor inspecting code diffs for secret leaks, "
                "credential exfiltration, injection flaws, and authorization bypasses. "
                "Ensure zero unredacted tokens or hazardous system calls (os.system, eval, exec) are committed."
            ),
            ReviewAgentType.INVARIANTS: (
                "# Role: Invariants Steward Reviewer\n\n"
                "Verify that all invariant clauses declared in the EIP remain strictly preserved. "
                "Evaluate proof obligations against the invariant matrix, checking for functional regressions "
                "and boundary condition violations."
            ),
            ReviewAgentType.ARCHITECTURE: (
                "# Role: Architecture Reviewer\n\n"
                "Assess separation of concerns, module boundaries, cyclic dependencies, and architectural patterns. "
                "Enforce MEES simplicity targets and prevent unnecessary coupling across domain boundaries."
            ),
            ReviewAgentType.CROSS_HARNESS: (
                "# Role: Independent Cross-Harness Reviewer\n\n"
                "Perform independent verification adhering strictly to separation of duties. "
                "Audit logic, edge cases, error handling, and test completeness from an alternate model harness."
            ),
            ReviewAgentType.DOMAIN: (
                "# Role: Domain Specialist Reviewer\n\n"
                "Provide deep domain-specific scrutiny on business logic, state machines, protocol compliance, "
                "and subsystem integration contracts."
            ),
            ReviewAgentType.ADVERSARIAL: ADVERSARIAL_VERIFIER_PROMPT,
        }

        template = ADVERSARIAL_VERIFIER_TEMPLATE if agent_type == ReviewAgentType.ADVERSARIAL else None

        return ReviewAgentSpec(
            name=f"{agent_type.value}-reviewer",
            agent_type=agent_type,
            harness=harness,
            system_prompt=prompts.get(agent_type, "Review proposed code changes."),
            strict=True,
            template=template,
        )

    def launch_review(
        self,
        artifact_id: str,
        builder_harness: str,
        diff_content: str,
        commit_sha: str = "HEAD",
        review_types: Optional[List[ReviewAgentType]] = None,
        custom_findings: Optional[List[ReviewFinding]] = None,
        mees: Optional[int] = None,
    ) -> ReviewAssessment:
        resolved_depth: Optional[ReviewDepth] = None
        if mees is not None and review_types is None:
            resolved_depth, review_types = select_review_depth_from_mees(mees)
        elif review_types is None:
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

        # If adversarial review is requested, inspect for high-risk constructs
        if ReviewAgentType.ADVERSARIAL in review_types:
            if "TODO" in diff_content or "FIXME" in diff_content:
                findings.append(
                    ReviewFinding(
                        reviewer=f"adversarial-{assigned_reviewer_harness}",
                        category="unverified_todo",
                        severity="warning",
                        description="Unresolved TODO/FIXME detected in proposed production change.",
                    )
                )

        blockers = [f for f in findings if f.severity == "blocker"]
        approved = cross_res.approved and len(blockers) == 0

        reviewers = [f"{rt.value}-{assigned_reviewer_harness}" for rt in review_types]

        summary = (
            f"Review approved with DSSE attestation (depth={resolved_depth.value if resolved_depth else 'standard'})."
            if approved
            else f"Review rejected due to {len(blockers)} blocker(s)."
        )

        return ReviewAssessment(
            artifact_id=artifact_id,
            approved=approved,
            reviewers_involved=reviewers,
            findings=findings,
            cross_harness_result=cross_res,
            summary=summary,
            review_depth=resolved_depth,
            mees_score=mees,
        )
