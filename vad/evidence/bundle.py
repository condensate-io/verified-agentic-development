import json
import hashlib
from typing import Any, Dict
from pydantic import BaseModel, Field, model_validator


class EvidenceRef(BaseModel):
    path: str | None = None
    digest: str


class AgentEvidence(BaseModel):
    builder: str
    verifier: str


class VerificationEvidence(BaseModel):
    passed: bool
    results: list[dict[str, Any]] = Field(default_factory=list)


class EffortEvidence(BaseModel):
    effort_type: str
    mees: int = Field(ge=0, le=100)
    policy: str
    changed_files: int = Field(ge=0)
    line_delta: int = Field(ge=0)
    new_dependencies: int = Field(ge=0)
    complexity_delta: int
    maintainability_delta: int


class TokenEvidence(BaseModel):
    budget: int = Field(ge=0)
    estimated: int | None = None
    used: int | None = None
    remaining: int | None = None
    optimization_notes: list[str] = Field(default_factory=list)


class PatchJournalEvidence(BaseModel):
    changed_files: list[str] = Field(default_factory=list)
    patch_digest: str
    rolled_back: bool = False
    blocker: str | None = None


class SignatureEvidence(BaseModel):
    key_id: str
    algorithm: str
    payload_digest: str
    created_at: str


class SignatureVerificationEvidence(BaseModel):
    key_id: str
    payload_digest: str
    verified: bool
    denial: str | None = None


class SignedEvidence(BaseModel):
    payload: dict[str, Any]
    signature: SignatureEvidence

    @property
    def payload_hash(self) -> str:
        return EvidenceBundle(self.payload).compute_hash()


class RunEvidence(BaseModel):
    schema_version: str = "1.0.0"
    run_id: str
    created_at: str
    eip: EvidenceRef
    proof_plan: EvidenceRef
    agents: AgentEvidence
    policy_decisions: list[dict[str, Any]] = Field(default_factory=list)
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    model_routes: list[dict[str, Any]] = Field(default_factory=list)
    memory_events: list[dict[str, Any]] = Field(default_factory=list)
    verification: VerificationEvidence | None = None
    release: dict[str, Any] | None = None
    feedback: dict[str, Any] | None = None
    effort: EffortEvidence
    tokens: TokenEvidence
    final_decision: str
    blocker: str | None = None

    @model_validator(mode="after")
    def enforce_effort_policy(self):
        if self.effort.mees < 50 and self.final_decision not in {"needs_human", "blocked"}:
            raise ValueError("MEES under 50 requires needs_human or blocked final decision")
        if self.effort.policy == "block" and self.final_decision not in {"needs_human", "blocked"}:
            raise ValueError("blocking MEES policy requires needs_human or blocked final decision")
        return self

class EvidenceBundle:
    def __init__(self, data: Dict[str, Any]):
        if isinstance(data, BaseModel):
            self.data = data.model_dump(mode="json")
        else:
            self.data = data

    def serialize(self) -> bytes:
        """
        Produce a deterministic JSON serialization.
        """
        return json.dumps(self.data, sort_keys=True, separators=(",", ":")).encode("utf-8")

    def compute_hash(self) -> str:
        """
        Compute a SHA256 hash of the deterministic serialization.
        """
        serialized = self.serialize()
        return hashlib.sha256(serialized).hexdigest()

    def is_tampered(self, expected_hash: str) -> bool:
        return self.compute_hash() != expected_hash
