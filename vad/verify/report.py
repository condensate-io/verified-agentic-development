from datetime import datetime, timezone
import hashlib
import json
from typing import Any, List, Optional
from pydantic import BaseModel, Field
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
