"""Evidence freshness validation for independent verification results."""

from datetime import datetime, timezone
import hashlib
import json

from vad.contracts.models import AssuranceLevel, EIP
from vad.proof.plan import ProofPlan, compute_eip_digest
from vad.verify.report import FreshnessCheckResult, IndependentVerificationResult

MAX_FUTURE_CLOCK_SKEW_SECONDS = 60.0


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def compute_proof_plan_digest(proof_plan: ProofPlan) -> str:
    """Compute a deterministic SHA-256 digest over canonical ProofPlan JSON."""
    data = proof_plan.model_dump(mode="json")
    data["mappings"] = sorted(
        data.get("mappings", []),
        key=lambda item: (item["obligation_id"], item["test_command"]),
    )
    data["required_manual_gates"] = sorted(data.get("required_manual_gates", []))
    canonical = json.dumps(data, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class FreshnessValidator:
    """Validates whether an IndependentVerificationResult remains fresh for target state."""

    def validate(
        self,
        result: IndependentVerificationResult,
        *,
        eip: EIP,
        proof_plan: ProofPlan,
        target_commit_sha: str,
        target_tree_hash: str,
        current_env_digest: str | None = None,
        dependency_changed: bool = False,
        now: datetime | None = None,
    ) -> FreshnessCheckResult:
        """Check digests, commit/tree/env bindings, dependency drift, TTL, and actor separation."""
        checked_at = _ensure_utc(now or datetime.now(timezone.utc))
        verified_at = _ensure_utc(result.verified_at)
        expires_at = _ensure_utc(result.expires_at)
        age_seconds = (checked_at - verified_at).total_seconds()
        policy = eip.effective_freshness_policy()
        reasons: list[str] = []

        if result.eip_digest != compute_eip_digest(eip):
            reasons.append("eip_digest mismatch: evidence does not match current EIP")

        if result.proof_plan_digest != compute_proof_plan_digest(proof_plan):
            reasons.append("proof_plan_digest mismatch: evidence does not match current ProofPlan")

        if policy.require_head_commit_match and result.commit_sha != target_commit_sha:
            reasons.append(
                f"commit_sha drift: expected {target_commit_sha!r}, got {result.commit_sha!r}"
            )

        if policy.require_tree_hash_match and result.tree_hash != target_tree_hash:
            reasons.append(
                f"tree_hash drift: expected {target_tree_hash!r}, got {result.tree_hash!r}"
            )

        if policy.require_env_digest_match and current_env_digest:
            if result.env_digest != current_env_digest:
                reasons.append(
                    f"env_digest drift: expected {current_env_digest!r}, got {result.env_digest!r}"
                )

        if policy.invalidate_on_dependency_change and dependency_changed:
            reasons.append("dependency change detected: verification evidence invalidated")

        if (verified_at - checked_at).total_seconds() > MAX_FUTURE_CLOCK_SKEW_SECONDS:
            reasons.append("verified_at timestamp is future-dated beyond 60s skew allowance")

        if checked_at > expires_at:
            reasons.append("evidence expired: checked_at exceeds expires_at")

        if age_seconds > policy.max_evidence_age_seconds:
            reasons.append(
                f"evidence age {age_seconds:.1f}s exceeds max_evidence_age_seconds "
                f"({policy.max_evidence_age_seconds}s)"
            )

        if eip.effective_assurance_level() != AssuranceLevel.A1_BASIC:
            if result.verifier_actor.strip() == result.builder_actor.strip():
                reasons.append("actor separation violated: verifier_actor must differ from builder_actor")
            if result.isolated_environment is not True:
                reasons.append("isolation violated: independent verification requires isolated_environment=True")

        return FreshnessCheckResult(
            is_fresh=len(reasons) == 0,
            reasons=reasons,
            checked_at=checked_at,
            age_seconds=age_seconds,
        )
