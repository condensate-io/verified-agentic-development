"""Rebase Guard: Verifies attestation stability across git rebases and detects stale reviews."""

from enum import Enum
import json
from typing import Optional
from pydantic import BaseModel
from vad.review.dsse_attestation import DSSEAttestor, DSSEEnvelope


class AttestationValidity(str, Enum):
    VALID = "valid"
    REBASE_STALE = "rebase_stale"
    INVALID = "invalid"


class RebaseCheckResult(BaseModel):
    commit_sha: str
    validity: AttestationValidity
    reason: str
    needs_re_review: bool
    patch_equivalent: bool = False


class RebaseGuard:
    """Verifies that DSSE review attestations remain valid following git rebases or branch updates."""

    def __init__(
        self,
        attestor: Optional[DSSEAttestor] = None,
        secret_key: bytes = b"vad-enterprise-signing-secret",
    ):
        self.attestor = attestor or DSSEAttestor()
        self.secret_key = secret_key

    def verify_commit_attestation(
        self,
        envelope: DSSEEnvelope,
        current_commit_sha: str,
        current_patch_id: Optional[str] = None,
    ) -> RebaseCheckResult:
        is_valid, payload_bytes, err = self.attestor.verify(envelope, secret_key=self.secret_key)
        if not is_valid or not payload_bytes:
            return RebaseCheckResult(
                commit_sha=current_commit_sha,
                validity=AttestationValidity.INVALID,
                reason=f"Envelope signature invalid or unverified: {err}",
                needs_re_review=True,
            )

        try:
            stmt = json.loads(payload_bytes.decode("utf-8"))
            subjects = stmt.get("subject", [])
            attested_sha = None
            attested_patch_id = stmt.get("predicate", {}).get("proof_summary", {}).get("patch_id")

            for s in subjects:
                digest = s.get("digest", {})
                if "sha1" in digest:
                    attested_sha = digest["sha1"]
                    break

            if not attested_sha:
                return RebaseCheckResult(
                    commit_sha=current_commit_sha,
                    validity=AttestationValidity.INVALID,
                    reason="Attestation payload missing subject commit digest",
                    needs_re_review=True,
                )

            # 1. Exact commit match -> 100% valid
            if attested_sha == current_commit_sha:
                return RebaseCheckResult(
                    commit_sha=current_commit_sha,
                    validity=AttestationValidity.VALID,
                    reason="Attestation matches exact commit SHA",
                    needs_re_review=False,
                    patch_equivalent=True,
                )

            # 2. SHA differs: check if patch-id is equivalent (rebase-stable equivalence)
            if current_patch_id and attested_patch_id and current_patch_id == attested_patch_id:
                return RebaseCheckResult(
                    commit_sha=current_commit_sha,
                    validity=AttestationValidity.VALID,
                    reason=f"Rebase detected from {attested_sha[:8]} -> {current_commit_sha[:8]}, but patch content is identical",
                    needs_re_review=False,
                    patch_equivalent=True,
                )

            # 3. SHA differs and patch differs or unverified -> Stale
            return RebaseCheckResult(
                commit_sha=current_commit_sha,
                validity=AttestationValidity.REBASE_STALE,
                reason=f"Rebase invalidated attestation: attested on {attested_sha[:8]}, current commit is {current_commit_sha[:8]} with differing patch",
                needs_re_review=True,
                patch_equivalent=False,
            )

        except Exception as e:
            return RebaseCheckResult(
                commit_sha=current_commit_sha,
                validity=AttestationValidity.INVALID,
                reason=f"Malformed attestation payload: {str(e)}",
                needs_re_review=True,
            )
