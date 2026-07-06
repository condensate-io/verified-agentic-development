from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator

from vad.policy.decisions import PolicyDecision
from vad.repo.patch_apply import apply_unified_diff


class DiffProposal(BaseModel):
    proposal_id: str = Field(default_factory=lambda: f"diff-proposal-{uuid4().hex}", max_length=120)
    run_id: str
    task_id: str
    submitted_by: str
    role: str
    patch_text: str = Field(min_length=1)
    patch_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    changed_files: list[str] = Field(min_length=1)
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    summary: str = Field(min_length=1, max_length=300)

    @field_validator("proposal_id", "run_id", "task_id", "submitted_by", "role")
    @classmethod
    def identifiers_must_be_safe(cls, value: str) -> str:
        if any(separator in value for separator in ["/", "\\", "\n", "\r", "\t"]):
            raise ValueError("diff proposal identifiers must not contain path or control separators")
        return value


class DiffApplyRecord(BaseModel):
    proposal_id: str
    run_id: str
    task_id: str
    applied: bool
    changed_files: list[str] = Field(default_factory=list)
    verifier_decision: PolicyDecision
    release_guardian_decision: PolicyDecision
    before_digest: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    after_file_digests: dict[str, str] = Field(default_factory=dict)
    blocker: str | None = None
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


def create_diff_proposal(
    *,
    run_id: str,
    task_id: str,
    submitted_by: str,
    role: str,
    patch_text: str,
    changed_files: list[str],
    summary: str,
) -> DiffProposal:
    return DiffProposal(
        run_id=run_id,
        task_id=task_id,
        submitted_by=submitted_by,
        role=role,
        patch_text=patch_text,
        patch_digest=_sha256_text(patch_text),
        changed_files=sorted(_normalize_changed_files(changed_files)),
        summary=summary,
    )


def apply_approved_diff_proposal(
    root: str | Path,
    proposal: DiffProposal,
    *,
    verifier_decision: PolicyDecision,
    release_guardian_decision: PolicyDecision,
) -> DiffApplyRecord:
    denial = _approval_blocker(verifier_decision, release_guardian_decision)
    if denial is not None:
        return DiffApplyRecord(
            proposal_id=proposal.proposal_id,
            run_id=proposal.run_id,
            task_id=proposal.task_id,
            applied=False,
            verifier_decision=verifier_decision,
            release_guardian_decision=release_guardian_decision,
            blocker=denial,
        )

    result = apply_unified_diff(root, proposal.patch_text)
    if not result.applied or result.journal is None:
        return DiffApplyRecord(
            proposal_id=proposal.proposal_id,
            run_id=proposal.run_id,
            task_id=proposal.task_id,
            applied=False,
            verifier_decision=verifier_decision,
            release_guardian_decision=release_guardian_decision,
            blocker=result.blocker,
        )
    changed_files = sorted(result.changed_files)
    if changed_files != sorted(proposal.changed_files):
        rollback = result.journal.rollback()
        blocker = "applied files do not match approved proposal"
        if not rollback.rolled_back:
            blocker = f"{blocker}; rollback failed: {rollback.blocker}"
        return DiffApplyRecord(
            proposal_id=proposal.proposal_id,
            run_id=proposal.run_id,
            task_id=proposal.task_id,
            applied=False,
            changed_files=changed_files,
            verifier_decision=verifier_decision,
            release_guardian_decision=release_guardian_decision,
            before_digest=result.journal.patch_digest,
            blocker=blocker,
        )
    return DiffApplyRecord(
        proposal_id=proposal.proposal_id,
        run_id=proposal.run_id,
        task_id=proposal.task_id,
        applied=True,
        changed_files=changed_files,
        verifier_decision=verifier_decision,
        release_guardian_decision=release_guardian_decision,
        before_digest=result.journal.patch_digest,
        after_file_digests=_file_digests(Path(root).resolve(), changed_files),
    )


def _approval_blocker(
    verifier_decision: PolicyDecision,
    release_guardian_decision: PolicyDecision,
) -> str | None:
    if not verifier_decision.allow:
        return "verifier approval required before diff apply"
    if not release_guardian_decision.allow:
        return "release guardian approval required before diff apply"
    return None


def _normalize_changed_files(paths: list[str]) -> list[str]:
    normalized = []
    for path in paths:
        cleaned = path.replace("\\", "/")
        if cleaned.startswith("/") or ".." in cleaned.split("/"):
            raise ValueError(f"unsafe changed file path: {path}")
        normalized.append(cleaned)
    return normalized


def _file_digests(root: Path, changed_files: list[str]) -> dict[str, str]:
    digests = {}
    for path in changed_files:
        target = (root / path).resolve()
        target.relative_to(root)
        digests[path] = hashlib.sha256(target.read_bytes()).hexdigest()
    return digests


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
