from datetime import datetime, timezone
import hashlib
import json
from typing import Any, List, Literal, Optional
from pydantic import BaseModel, Field, model_validator
from vad.contracts.models import AssuranceLevel
from vad.proof.plan import VerifyStatus


class VerifierResult(BaseModel):
    obligation_id: str
    status: VerifyStatus
    output: str
    error: Optional[str] = None
    tool_call: dict[str, Any] | None = None


class VerifierReport(BaseModel):
    timestamp: datetime
    results: List[VerifierResult]
    passed: bool

    def to_json(self) -> str:
        return self.model_dump_json(indent=2)


class LadderStepResult(BaseModel):
    step_name: str
    order: int
    cost_tier: str  # lowest | low | medium | high | highest
    status: str  # PASS | FAIL | SKIPPED
    output: str = ""
    error: Optional[str] = None
    duration_ms: float = 0.0


class VerificationReceipt(BaseModel):
    receipt_id: str
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    commit_sha: str = "HEAD"
    steps: List[LadderStepResult] = Field(default_factory=list)
    passed: bool = True
    first_failure: Optional[str] = None
    mees_score: Optional[int] = None
    receipt_sha: str = ""

    def compute_sha(self) -> str:
        """Computes a deterministic SHA-256 stamp over the receipt payload."""
        data = {
            "receipt_id": self.receipt_id,
            "timestamp": self.timestamp,
            "commit_sha": self.commit_sha,
            "steps": [s.model_dump() for s in self.steps],
            "passed": self.passed,
            "first_failure": self.first_failure,
            "mees_score": self.mees_score,
        }
        canonical = json.dumps(data, sort_keys=True)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def stamp(self) -> "VerificationReceipt":
        """Calculates and assigns receipt_sha."""
        self.receipt_sha = self.compute_sha()
        return self


class BuilderClaim(BaseModel):
    """Non-authoritative verification claim submitted by a builder."""

    claim_id: str
    builder_actor: str
    commit_sha: str
    tree_hash: str
    summary: str
    claimed_passed: bool
    authoritative: Literal[False] = False


class FreshnessCheckResult(BaseModel):
    """Result of validating verification evidence freshness against target state."""

    is_fresh: bool
    reasons: list[str] = Field(default_factory=list)
    checked_at: datetime
    age_seconds: float | None = None


class IndependentVerificationResult(BaseModel):
    """Authoritative verification result produced by an independent verifier."""

    verification_id: str
    eip_digest: str
    proof_plan_digest: str
    commit_sha: str
    tree_hash: str
    env_digest: str
    builder_actor: str
    verifier_actor: str
    isolated_environment: bool
    assurance_achieved: AssuranceLevel
    passed: bool
    obligation_results: list[VerifierResult]
    verified_at: datetime
    expires_at: datetime
    receipt_sha: str = ""

    def compute_sha(self) -> str:
        """Compute a deterministic SHA-256 stamp over the independent verification payload."""
        data = {
            "verification_id": self.verification_id,
            "eip_digest": self.eip_digest,
            "proof_plan_digest": self.proof_plan_digest,
            "commit_sha": self.commit_sha,
            "tree_hash": self.tree_hash,
            "env_digest": self.env_digest,
            "builder_actor": self.builder_actor,
            "verifier_actor": self.verifier_actor,
            "isolated_environment": self.isolated_environment,
            "assurance_achieved": self.assurance_achieved.value,
            "passed": self.passed,
            "obligation_results": [r.model_dump(mode="json") for r in self.obligation_results],
            "verified_at": self.verified_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
        }
        canonical = json.dumps(data, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def stamp(self) -> "IndependentVerificationResult":
        """Calculate and assign receipt_sha."""
        self.receipt_sha = self.compute_sha()
        return self

    @model_validator(mode="after")
    def validate_actor_separation_and_stamp(self) -> "IndependentVerificationResult":
        if not self.builder_actor.strip() or not self.verifier_actor.strip():
            raise ValueError("builder_actor and verifier_actor must be non-empty")
        if self.verifier_actor.strip() == self.builder_actor.strip() and (
            self.isolated_environment
            or self.assurance_achieved
            in {
                AssuranceLevel.A2_INDEPENDENT,
                AssuranceLevel.A3_ATTESTED,
                AssuranceLevel.A4_RESTRICTED,
            }
        ):
            raise ValueError(
                "verifier_actor must be distinct from builder_actor for independent verification"
            )
        if not self.receipt_sha:
            self.receipt_sha = self.compute_sha()
        return self
