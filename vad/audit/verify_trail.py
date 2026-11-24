"""Independent Operator Audit Trail Verifier over VAD's authoritative SQLite ledger."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from vad.contracts.models import AssuranceLevel, RiskTier
from vad.control_plane.checkpoints import STAGE_ORDER, RunCheckpoint
from vad.evidence.bundle import EvidenceBundle, RunEvidence
from vad.memory.authority_guard import _find_forbidden_fields
from vad.release.decision import ReleaseDecision
from vad.review.dsse_attestation import (
    DSSEAttestor,
    DSSEEnvelope,
    decode_attestation_statement,
)
from vad.server.db.store import ServerStore, StoredRunEvidence
from vad.signing.ed25519_local import DSSEKeyProvider


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class AuditCheckItem(BaseModel):
    """Single deterministic check result in an operator audit trail report."""

    check: str
    passed: bool
    detail: str


class AuditTrailReport(BaseModel):
    """Comprehensive audit verification report for a VAD run."""

    run_id: str
    verified: bool
    evidence_digest: str | None = None
    checks: list[AuditCheckItem] = Field(default_factory=list)
    failures: list[str] = Field(default_factory=list)
    checked_at: datetime = Field(default_factory=_utc_now)


def _check_evidence_integrity(
    store: ServerStore,
    run_id: str,
) -> tuple[AuditCheckItem, StoredRunEvidence | None]:
    try:
        stored = store.load_run_evidence(run_id)
    except KeyError:
        return (
            AuditCheckItem(
                check="evidence_integrity",
                passed=False,
                detail=f"Run evidence not found for run_id '{run_id}'",
            ),
            None,
        )
    except Exception as exc:
        return (
            AuditCheckItem(
                check="evidence_integrity",
                passed=False,
                detail=f"Run evidence integrity check failed: {exc}",
            ),
            None,
        )

    recomputed = EvidenceBundle(stored.evidence).compute_hash()
    if recomputed != stored.evidence_digest:
        return (
            AuditCheckItem(
                check="evidence_integrity",
                passed=False,
                detail=(
                    f"Evidence digest mismatch: stored={stored.evidence_digest}, "
                    f"recomputed={recomputed}"
                ),
            ),
            None,
        )

    return (
        AuditCheckItem(
            check="evidence_integrity",
            passed=True,
            detail=f"Verified evidence digest {stored.evidence_digest}",
        ),
        stored,
    )


def _check_digests_present(evidence: RunEvidence | None) -> AuditCheckItem:
    if evidence is None:
        return AuditCheckItem(
            check="eip_and_proof_plan_digests",
            passed=False,
            detail="Run evidence unavailable; cannot verify EIP and proof plan digests",
        )
    eip_digest = (evidence.eip.digest or "").strip()
    proof_digest = (evidence.proof_plan.digest or "").strip()
    missing: list[str] = []
    if not eip_digest:
        missing.append("eip.digest")
    if not proof_digest:
        missing.append("proof_plan.digest")
    if missing:
        return AuditCheckItem(
            check="eip_and_proof_plan_digests",
            passed=False,
            detail=f"Missing required digest(s): {', '.join(missing)}",
        )
    return AuditCheckItem(
        check="eip_and_proof_plan_digests",
        passed=True,
        detail="Both eip.digest and proof_plan.digest are present",
    )


def _allows_self_verification(
    evidence: RunEvidence,
    latest_decision: ReleaseDecision | None,
) -> bool:
    if latest_decision is not None:
        return (
            latest_decision.risk_tier == RiskTier.LOW
            and latest_decision.assurance_required == AssuranceLevel.A1_BASIC
            and latest_decision.assurance_achieved == AssuranceLevel.A1_BASIC
        )
    if isinstance(evidence.release, dict):
        risk_tier = str(evidence.release.get("risk_tier", "")).strip().lower()
        assurance = str(
            evidence.release.get("assurance_level")
            or evidence.release.get("assurance_required")
            or ""
        ).strip().upper()
        independent_required = bool(evidence.release.get("independent_verification_required", False))
        return risk_tier == RiskTier.LOW.value and assurance in {"A1_BASIC", "A1"} and not independent_required
    return False


def _check_actor_separation(
    evidence: RunEvidence | None,
    latest_decision: ReleaseDecision | None,
) -> AuditCheckItem:
    if evidence is None:
        return AuditCheckItem(
            check="actor_separation",
            passed=False,
            detail="Run evidence unavailable; cannot verify actor separation",
        )
    builder = evidence.agents.builder.strip()
    verifier = evidence.agents.verifier.strip()
    if not builder or not verifier:
        return AuditCheckItem(
            check="actor_separation",
            passed=False,
            detail="Builder and verifier actor identifiers must both be non-empty",
        )
    if builder != verifier:
        return AuditCheckItem(
            check="actor_separation",
            passed=True,
            detail=f"Builder '{builder}' is distinct from verifier '{verifier}'",
        )
    if _allows_self_verification(evidence, latest_decision):
        return AuditCheckItem(
            check="actor_separation",
            passed=True,
            detail=f"Same builder/verifier '{builder}' permitted for low-risk A1_BASIC run",
        )
    return AuditCheckItem(
        check="actor_separation",
        passed=False,
        detail=f"Separation-of-duties violation: builder '{builder}' cannot verify own run",
    )


def _check_verification_passed(evidence: RunEvidence | None) -> AuditCheckItem:
    if evidence is None:
        return AuditCheckItem(
            check="verification_passed",
            passed=False,
            detail="Run evidence unavailable; cannot verify verification result",
        )
    if evidence.verification is None:
        return AuditCheckItem(
            check="verification_passed",
            passed=False,
            detail="Verification evidence is missing",
        )
    if evidence.verification.passed is not True:
        return AuditCheckItem(
            check="verification_passed",
            passed=False,
            detail="Verification evidence indicates verification did not pass",
        )
    return AuditCheckItem(
        check="verification_passed",
        passed=True,
        detail="Verification evidence passed",
    )


def _resolve_dsse_envelope(
    dsse_envelope: DSSEEnvelope | None,
    evidence: RunEvidence | None,
) -> tuple[DSSEEnvelope | None, str | None]:
    if dsse_envelope is not None:
        return dsse_envelope, None
    if evidence is not None and isinstance(evidence.release, dict):
        raw_env = evidence.release.get("dsse_envelope")
        if raw_env is not None:
            if isinstance(raw_env, DSSEEnvelope):
                return raw_env, None
            try:
                return DSSEEnvelope.model_validate(raw_env), None
            except ValidationError as exc:
                return None, f"Invalid stored dsse_envelope in evidence.release: {exc}"
    return None, None


def _check_dsse_attestation(
    evidence: RunEvidence | None,
    *,
    dsse_envelope: DSSEEnvelope | None,
    key_provider: DSSEKeyProvider | None,
    expected_commit_sha: str | None,
) -> AuditCheckItem:
    envelope, parse_err = _resolve_dsse_envelope(dsse_envelope, evidence)
    if parse_err is not None:
        return AuditCheckItem(check="dsse_attestation", passed=False, detail=parse_err)

    if envelope is None:
        if expected_commit_sha is not None:
            return AuditCheckItem(
                check="dsse_attestation",
                passed=False,
                detail=(
                    f"expected_commit_sha '{expected_commit_sha}' provided but no DSSE "
                    "envelope was supplied or stored in evidence.release"
                ),
            )
        return AuditCheckItem(
            check="dsse_attestation",
            passed=True,
            detail="No DSSE envelope provided or stored; cryptographic check skipped",
        )

    attestor = DSSEAttestor(key_provider=key_provider, strict=True)
    try:
        valid, payload_bytes, err = attestor.verify(envelope)
    except Exception as exc:
        return AuditCheckItem(
            check="dsse_attestation",
            passed=False,
            detail=f"DSSE verification error: {exc}",
        )

    if not valid or payload_bytes is None:
        return AuditCheckItem(
            check="dsse_attestation",
            passed=False,
            detail=f"DSSE signature verification failed: {err or 'invalid signature'}",
        )

    try:
        statement = decode_attestation_statement(envelope)
    except ValueError as exc:
        return AuditCheckItem(
            check="dsse_attestation",
            passed=False,
            detail=f"DSSE statement decoding failed: {exc}",
        )

    predicate = statement.get("predicate")
    if isinstance(predicate, dict) and "decision" in predicate:
        decision = str(predicate["decision"]).strip().lower()
        if decision not in {"approved", "passed", "pass"}:
            return AuditCheckItem(
                check="dsse_attestation",
                passed=False,
                detail=f"DSSE attestation predicate decision is not approved: {predicate['decision']!r}",
            )

    if expected_commit_sha is not None:
        subjects = statement.get("subject", [])
        matched = False
        if isinstance(subjects, list):
            for subj in subjects:
                if not isinstance(subj, dict):
                    continue
                digest = subj.get("digest", {})
                if isinstance(digest, dict) and (
                    digest.get("sha1") == expected_commit_sha
                    or digest.get("sha256") == expected_commit_sha
                ):
                    matched = True
                    break
        if not matched:
            return AuditCheckItem(
                check="dsse_attestation",
                passed=False,
                detail=f"DSSE attestation subject commit SHA does not match expected '{expected_commit_sha}'",
            )

    return AuditCheckItem(
        check="dsse_attestation",
        passed=True,
        detail="DSSE cryptographic signature and attestation statement verified",
    )


def _check_release_decision(
    store: ServerStore,
    run_id: str,
    stored: StoredRunEvidence | None,
    decisions: list[ReleaseDecision],
    *,
    require_release_decision: bool,
) -> AuditCheckItem:
    if not decisions:
        if require_release_decision:
            return AuditCheckItem(
                check="release_decision",
                passed=False,
                detail=f"No release decision recorded for run_id '{run_id}'",
            )
        return AuditCheckItem(
            check="release_decision",
            passed=True,
            detail="Release decision not required and none recorded",
        )

    latest = decisions[-1]
    linked_digest = (latest.evidence_digest or "").strip()
    if not linked_digest:
        return AuditCheckItem(
            check="release_decision",
            passed=False,
            detail=f"Release decision '{latest.decision_id}' has empty evidence_digest",
        )

    if stored is None:
        return AuditCheckItem(
            check="release_decision",
            passed=False,
            detail="Run evidence unavailable to verify release decision digest linkage",
        )

    if linked_digest != stored.evidence_digest:
        return AuditCheckItem(
            check="release_decision",
            passed=False,
            detail=(
                f"Release decision '{latest.decision_id}' evidence_digest '{linked_digest}' "
                f"does not match stored '{stored.evidence_digest}'"
            ),
        )

    final_decision = stored.evidence.final_decision.strip().lower()
    if final_decision in {"passed", "completed"} and not latest.can_release:
        return AuditCheckItem(
            check="release_decision",
            passed=False,
            detail=(
                f"Release decision '{latest.decision_id}' has can_release=False "
                f"for final_decision='{stored.evidence.final_decision}'"
            ),
        )

    builder = stored.evidence.agents.builder.strip()
    approvers = [a.strip() for a in latest.recorded_approvers if a.strip()]
    if builder and builder in approvers:
        return AuditCheckItem(
            check="release_decision",
            passed=False,
            detail=(
                f"Builder self-approval detected: '{builder}' present in "
                "release_decision.recorded_approvers"
            ),
        )

    for event in store.list_approval_events(run_id=run_id):
        if event.decision.allow and event.actor.strip() == builder:
            return AuditCheckItem(
                check="release_decision",
                passed=False,
                detail=(
                    f"Builder self-approval detected in approval_events: "
                    f"approval_id='{event.approval_id}', actor='{builder}'"
                ),
            )

    return AuditCheckItem(
        check="release_decision",
        passed=True,
        detail=(
            f"Release decision '{latest.decision_id}' verified "
            f"(outcome={latest.outcome.value}, can_release={latest.can_release})"
        ),
    )


def _check_checkpoint_chain(
    checkpoints: list[RunCheckpoint],
    *,
    require_checkpoints: bool,
) -> AuditCheckItem:
    if not checkpoints:
        if require_checkpoints:
            return AuditCheckItem(
                check="checkpoint_chain",
                passed=False,
                detail="No durable checkpoints recorded for run",
            )
        return AuditCheckItem(
            check="checkpoint_chain",
            passed=True,
            detail="Checkpoints not required and none recorded",
        )

    last_by_work_item: dict[str, RunCheckpoint] = {}
    violations: list[str] = []

    for cp in checkpoints:
        prev = last_by_work_item.get(cp.work_item_id)
        if prev is not None:
            if cp.fence_token < prev.fence_token:
                violations.append(
                    f"fence_token regression for work_item '{cp.work_item_id}': "
                    f"{prev.fence_token} -> {cp.fence_token}"
                )
            if STAGE_ORDER[cp.stage] < STAGE_ORDER[prev.stage]:
                violations.append(
                    f"stage regression for work_item '{cp.work_item_id}': "
                    f"{prev.stage.value} -> {cp.stage.value}"
                )
        last_by_work_item[cp.work_item_id] = cp

    if violations:
        return AuditCheckItem(
            check="checkpoint_chain",
            passed=False,
            detail="; ".join(violations),
        )

    return AuditCheckItem(
        check="checkpoint_chain",
        passed=True,
        detail=(
            f"Verified {len(checkpoints)} monotonic checkpoint(s) across "
            f"{len(last_by_work_item)} work item(s)"
        ),
    )


def _check_memory_authority(evidence: RunEvidence | None) -> AuditCheckItem:
    if evidence is None:
        return AuditCheckItem(
            check="memory_authority",
            passed=False,
            detail="Run evidence unavailable; cannot inspect memory_events",
        )
    findings = _find_forbidden_fields(evidence.memory_events, path="memory_events")
    if findings:
        return AuditCheckItem(
            check="memory_authority",
            passed=False,
            detail=f"Forbidden authority-override key(s) in memory_events: {', '.join(findings)}",
        )
    return AuditCheckItem(
        check="memory_authority",
        passed=True,
        detail=f"Inspected {len(evidence.memory_events)} memory event(s); no authority overrides found",
    )


def verify_run_audit_trail(
    store: ServerStore,
    run_id: str,
    *,
    dsse_envelope: DSSEEnvelope | None = None,
    key_provider: DSSEKeyProvider | None = None,
    expected_commit_sha: str | None = None,
    require_checkpoints: bool = True,
    require_release_decision: bool = True,
) -> AuditTrailReport:
    """Independently verify a run's complete audit trail from VAD's authoritative ledger."""
    integrity_check, stored = _check_evidence_integrity(store, run_id)
    evidence: RunEvidence | None = stored.evidence if stored is not None else None

    decisions = store.list_release_decisions(run_id=run_id)
    latest_decision = decisions[-1] if decisions else None
    checkpoints = store.list_checkpoints(run_id=run_id)

    checks: list[AuditCheckItem] = [
        integrity_check,
        _check_digests_present(evidence),
        _check_actor_separation(evidence, latest_decision),
        _check_verification_passed(evidence),
        _check_dsse_attestation(
            evidence,
            dsse_envelope=dsse_envelope,
            key_provider=key_provider,
            expected_commit_sha=expected_commit_sha,
        ),
        _check_release_decision(
            store,
            run_id,
            stored,
            decisions,
            require_release_decision=require_release_decision,
        ),
        _check_checkpoint_chain(
            checkpoints,
            require_checkpoints=require_checkpoints,
        ),
        _check_memory_authority(evidence),
    ]

    failures = [f"{item.check}: {item.detail}" for item in checks if not item.passed]
    return AuditTrailReport(
        run_id=run_id,
        verified=len(checks) > 0 and all(item.passed for item in checks),
        evidence_digest=stored.evidence_digest if stored is not None else None,
        checks=checks,
        failures=failures,
        checked_at=_utc_now(),
    )
