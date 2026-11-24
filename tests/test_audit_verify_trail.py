from __future__ import annotations

import base64
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from vad.audit.verify_trail import AuditTrailReport, verify_run_audit_trail
from vad.contracts.models import AssuranceLevel, RiskTier
from vad.control_plane.checkpoints import CheckpointStage, RunCheckpoint
from vad.evidence.bundle import (
    AgentEvidence,
    EffortEvidence,
    EvidenceRef,
    RunEvidence,
    TokenEvidence,
    VerificationEvidence,
)
from vad.policy.autonomy_matrix import PolicyStage
from vad.policy.decisions import PolicyDecision
from vad.release.decision import ReleaseDecision, ReleaseOutcome
from vad.review.dsse_attestation import DSSEAttestor, DSSEEnvelope
from vad.server.db.store import ApprovalEvent, ServerStore
from vad.signing.ed25519_local import LocalEd25519KeyProvider

_TEST_SEED = bytes.fromhex(
    "9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60"
)
_ALT_SEED = bytes.fromhex(
    "4ccd089b28ff96da9db6c346ec114e0f5b8a319f35aba624da8cf6ed4fb8a6fb"
)
_COMMIT_SHA = "a1b2c3d4e5f60718293a4b5c6d7e8f9012345678"


def _make_evidence(
    *,
    run_id: str = "run-audit-1",
    builder: str = "codex-builder",
    verifier: str = "claude-verifier",
    eip_digest: str = "1" * 64,
    proof_digest: str = "2" * 64,
    verification_passed: bool | None = True,
    memory_events: list[dict] | None = None,
    release: dict | None = None,
    final_decision: str = "passed",
) -> RunEvidence:
    verification = (
        None
        if verification_passed is None
        else VerificationEvidence(
            passed=verification_passed,
            results=[{"obligation_id": "po-unit", "status": "passed"}],
        )
    )
    return RunEvidence(
        run_id=run_id,
        created_at="2026-07-15T10:00:00+00:00",
        eip=EvidenceRef(path="examples/eip/sample.yaml", digest=eip_digest),
        proof_plan=EvidenceRef(path="examples/proof/sample-proof-plan.yaml", digest=proof_digest),
        agents=AgentEvidence(builder=builder, verifier=verifier),
        verification=verification,
        memory_events=memory_events if memory_events is not None else [
            {"event": "memory_requirement", "scope": "project", "purpose": "read_lessons"}
        ],
        release=release if release is not None else {"evaluated": True},
        effort=EffortEvidence(
            effort_type="feature",
            mees=92,
            policy="pass",
            changed_files=2,
            line_delta=24,
            new_dependencies=0,
            complexity_delta=0,
            maintainability_delta=1,
        ),
        tokens=TokenEvidence(budget=10000, used=1200, remaining=8800),
        final_decision=final_decision,
    )


def _seed_checkpoints(
    store: ServerStore,
    run_id: str = "run-audit-1",
    *,
    work_item_id: str = "wi-1",
) -> None:
    t0 = datetime(2026, 7, 15, 10, 0, tzinfo=timezone.utc)
    stages = [
        (CheckpointStage.EIP_CREATED, 1, "planner-1", "planner", 0),
        (CheckpointStage.PROOF_PLANNED, 1, "planner-1", "planner", 10),
        (CheckpointStage.DIFF_PROPOSED, 2, "codex-builder", "builder", 20),
        (CheckpointStage.VERIFIED, 3, "claude-verifier", "verifier", 30),
        (CheckpointStage.RELEASED, 4, "guardian-alice", "release_guardian", 40),
    ]
    for idx, (stage, fence_token, actor, role, offset_sec) in enumerate(stages, start=1):
        store.save_checkpoint(
            RunCheckpoint(
                checkpoint_id=f"{run_id}-cp-{idx}",
                run_id=run_id,
                work_item_id=work_item_id,
                stage=stage,
                fence_token=fence_token,
                actor=actor,
                role=role,
                artifact_digests={"eip": "1" * 64},
                state_snapshot={"step": idx},
                created_at=t0 + timedelta(seconds=offset_sec),
            )
        )


def _make_release_decision(
    run_id: str,
    evidence_digest: str,
    *,
    eip_digest: str = "1" * 64,
    risk_tier: RiskTier = RiskTier.HIGH,
    assurance_required: AssuranceLevel = AssuranceLevel.A3_ATTESTED,
    assurance_achieved: AssuranceLevel = AssuranceLevel.A3_ATTESTED,
    outcome: ReleaseOutcome = ReleaseOutcome.APPROVED_WITH_HUMAN,
    can_release: bool = True,
    recorded_approvers: list[str] | None = None,
) -> ReleaseDecision:
    return ReleaseDecision(
        run_id=run_id,
        eip_digest=eip_digest,
        evidence_digest=evidence_digest,
        risk_tier=risk_tier,
        stage=PolicyStage.DEPLOY,
        assurance_required=assurance_required,
        assurance_achieved=assurance_achieved,
        outcome=outcome,
        can_release=can_release,
        reasons=["Independent verification and human guardian sign-off recorded"],
        required_signoffs=1 if risk_tier == RiskTier.HIGH else 0,
        recorded_approvers=recorded_approvers if recorded_approvers is not None else ["guardian-alice"],
        decided_at=datetime(2026, 7, 15, 10, 5, tzinfo=timezone.utc),
    )


def _make_ed25519_attestation(
    seed: bytes = _TEST_SEED,
    commit_sha: str = _COMMIT_SHA,
    decision: str = "approved",
) -> tuple[LocalEd25519KeyProvider, DSSEEnvelope]:
    provider = LocalEd25519KeyProvider.from_seed(seed, key_id="ed25519-test-key")
    attestor = DSSEAttestor(key_provider=provider, strict=True)
    envelope = attestor.create_review_attestation(
        commit_sha=commit_sha,
        review_decision=decision,
        reviewer_agent="claude-verifier",
        proof_summary={"passed": True, "obligations": 1},
        eip_digest="1" * 64,
        proof_plan_digest="2" * 64,
        assurance_level="A3_ATTESTED",
    )
    return provider, envelope


def _check_map(report: AuditTrailReport) -> dict[str, bool]:
    return {item.check: item.passed for item in report.checks}


def test_happy_path_complete_run_passes_all_checks(tmp_path: Path):
    store = ServerStore(tmp_path / "audit_happy.sqlite3")
    provider, envelope = _make_ed25519_attestation()
    evidence = _make_evidence(release={"evaluated": True, "dsse_envelope": envelope.model_dump(mode="json")})
    stored = store.save_run_evidence(evidence)
    store.save_release_decision(_make_release_decision(stored.run_id, stored.evidence_digest))
    _seed_checkpoints(store, stored.run_id)
    # Denied self-approval attempt by builder must NOT fail audit (policy gate blocked it).
    store.save_approval_event(
        ApprovalEvent(
            approval_id="approval-denied-self",
            run_id=stored.run_id,
            actor="codex-builder",
            action="approve_release",
            decision=PolicyDecision(allow=False, denials=["builder may not approve own run"], requires_human=True),
            evidence_digest=stored.evidence_digest,
        )
    )

    report = verify_run_audit_trail(
        store,
        stored.run_id,
        dsse_envelope=envelope,
        key_provider=provider,
        expected_commit_sha=_COMMIT_SHA,
    )

    assert report.verified is True
    assert report.run_id == stored.run_id
    assert report.evidence_digest == stored.evidence_digest
    assert report.failures == []
    assert report.checked_at.tzinfo == timezone.utc
    assert len(report.checks) == 8
    assert all(item.passed for item in report.checks)

    # Also verifies when DSSE envelope is loaded solely from evidence.release
    report_from_stored = verify_run_audit_trail(
        store,
        stored.run_id,
        key_provider=provider,
        expected_commit_sha=_COMMIT_SHA,
    )
    assert report_from_stored.verified is True


def test_tampered_run_evidence_payload_fails_evidence_integrity(tmp_path: Path):
    db_path = tmp_path / "audit_tamper.sqlite3"
    store = ServerStore(db_path)
    provider, envelope = _make_ed25519_attestation()
    evidence = _make_evidence()
    stored = store.save_run_evidence(evidence)
    store.save_release_decision(_make_release_decision(stored.run_id, stored.evidence_digest))
    _seed_checkpoints(store, stored.run_id)

    # Mutate payload_json directly in SQLite without updating evidence_digest.
    mutated = evidence.model_dump(mode="json")
    mutated["agents"]["verifier"] = "tampered-verifier"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE run_evidence SET payload_json = ? WHERE run_id = ?",
            (json.dumps(mutated, sort_keys=True), stored.run_id),
        )

    report = verify_run_audit_trail(
        store,
        stored.run_id,
        dsse_envelope=envelope,
        key_provider=provider,
        expected_commit_sha=_COMMIT_SHA,
    )

    assert report.verified is False
    assert report.evidence_digest is None
    checks = _check_map(report)
    assert checks["evidence_integrity"] is False
    assert any("evidence_integrity" in failure for failure in report.failures)


def test_tampered_dsse_ed25519_signature_and_commit_mismatch_fail(tmp_path: Path):
    store = ServerStore(tmp_path / "audit_dsse.sqlite3")
    provider, envelope = _make_ed25519_attestation()
    evidence = _make_evidence()
    stored = store.save_run_evidence(evidence)
    store.save_release_decision(_make_release_decision(stored.run_id, stored.evidence_digest))
    _seed_checkpoints(store, stored.run_id)

    raw_sig = bytearray(base64.b64decode(envelope.signatures[0].sig))
    raw_sig[0] ^= 0xFF
    tampered_envelope = envelope.model_copy(
        update={
            "signatures": [
                envelope.signatures[0].model_copy(
                    update={"sig": base64.b64encode(bytes(raw_sig)).decode("utf-8")}
                )
            ]
        }
    )

    report_tampered = verify_run_audit_trail(
        store,
        stored.run_id,
        dsse_envelope=tampered_envelope,
        key_provider=provider,
        expected_commit_sha=_COMMIT_SHA,
    )
    assert report_tampered.verified is False
    assert _check_map(report_tampered)["dsse_attestation"] is False

    # Wrong key provider must also fail.
    wrong_provider = LocalEd25519KeyProvider.from_seed(_ALT_SEED, key_id="wrong-key")
    report_wrong_key = verify_run_audit_trail(
        store,
        stored.run_id,
        dsse_envelope=envelope,
        key_provider=wrong_provider,
        expected_commit_sha=_COMMIT_SHA,
    )
    assert report_wrong_key.verified is False
    assert _check_map(report_wrong_key)["dsse_attestation"] is False

    # Valid signature but wrong commit SHA must fail.
    report_wrong_sha = verify_run_audit_trail(
        store,
        stored.run_id,
        dsse_envelope=envelope,
        key_provider=provider,
        expected_commit_sha="deadbeef" * 5,
    )
    assert report_wrong_sha.verified is False
    assert _check_map(report_wrong_sha)["dsse_attestation"] is False


def test_builder_equals_verifier_fails_separation_unless_low_risk_a1(tmp_path: Path):
    store = ServerStore(tmp_path / "audit_separation.sqlite3")
    evidence = _make_evidence(
        run_id="run-self-med",
        builder="same-agent",
        verifier="same-agent",
    )
    stored = store.save_run_evidence(evidence)
    store.save_release_decision(
        _make_release_decision(
            stored.run_id,
            stored.evidence_digest,
            risk_tier=RiskTier.MEDIUM,
            assurance_required=AssuranceLevel.A2_INDEPENDENT,
            assurance_achieved=AssuranceLevel.A2_INDEPENDENT,
            outcome=ReleaseOutcome.AUTONOMOUS_RELEASE,
            recorded_approvers=[],
        )
    )
    _seed_checkpoints(store, stored.run_id)

    report_med = verify_run_audit_trail(store, stored.run_id)
    assert report_med.verified is False
    assert _check_map(report_med)["actor_separation"] is False

    # Low-risk A1_BASIC explicitly permits builder == verifier.
    evidence_low = _make_evidence(
        run_id="run-self-low",
        builder="same-agent",
        verifier="same-agent",
    )
    stored_low = store.save_run_evidence(evidence_low)
    store.save_release_decision(
        _make_release_decision(
            stored_low.run_id,
            stored_low.evidence_digest,
            risk_tier=RiskTier.LOW,
            assurance_required=AssuranceLevel.A1_BASIC,
            assurance_achieved=AssuranceLevel.A1_BASIC,
            outcome=ReleaseOutcome.AUTONOMOUS_RELEASE,
            recorded_approvers=[],
        )
    )
    _seed_checkpoints(store, stored_low.run_id)

    report_low = verify_run_audit_trail(store, stored_low.run_id)
    assert report_low.verified is True
    assert _check_map(report_low)["actor_separation"] is True


def test_missing_release_decision_and_builder_self_approval_fail(tmp_path: Path):
    store = ServerStore(tmp_path / "audit_release.sqlite3")
    evidence = _make_evidence(run_id="run-rel-check")
    stored = store.save_run_evidence(evidence)
    _seed_checkpoints(store, stored.run_id)

    # 1. Missing release decision when required
    report_missing = verify_run_audit_trail(store, stored.run_id)
    assert report_missing.verified is False
    assert _check_map(report_missing)["release_decision"] is False

    # 2. Builder in recorded_approvers
    store.save_release_decision(
        _make_release_decision(
            stored.run_id,
            stored.evidence_digest,
            recorded_approvers=["codex-builder"],
        )
    )
    report_self_approve = verify_run_audit_trail(store, stored.run_id)
    assert report_self_approve.verified is False
    assert _check_map(report_self_approve)["release_decision"] is False

    # 3. Valid recorded_approvers, but an allowed ApprovalEvent by builder in ledger
    store.save_release_decision(
        ReleaseDecision(
            decision_id="dec-valid-later",
            run_id=stored.run_id,
            eip_digest="1" * 64,
            evidence_digest=stored.evidence_digest,
            risk_tier=RiskTier.HIGH,
            stage=PolicyStage.DEPLOY,
            assurance_required=AssuranceLevel.A3_ATTESTED,
            assurance_achieved=AssuranceLevel.A3_ATTESTED,
            outcome=ReleaseOutcome.APPROVED_WITH_HUMAN,
            can_release=True,
            recorded_approvers=["guardian-alice"],
            required_signoffs=1,
            decided_at=datetime(2026, 7, 15, 10, 10, tzinfo=timezone.utc),
        )
    )
    store.save_approval_event(
        ApprovalEvent(
            approval_id="approval-allowed-self",
            run_id=stored.run_id,
            actor="codex-builder",
            action="approve_release",
            decision=PolicyDecision(allow=True, reasons=["bypassed"]),
            evidence_digest=stored.evidence_digest,
        )
    )
    report_event_self = verify_run_audit_trail(store, stored.run_id)
    assert report_event_self.verified is False
    assert _check_map(report_event_self)["release_decision"] is False


def test_checkpoint_fence_token_regression_and_missing_checkpoints_fail(tmp_path: Path):
    store = ServerStore(tmp_path / "audit_cp.sqlite3")
    evidence = _make_evidence(run_id="run-cp-regress")
    stored = store.save_run_evidence(evidence)
    store.save_release_decision(_make_release_decision(stored.run_id, stored.evidence_digest))

    # Missing checkpoints fails when require_checkpoints=True
    report_empty = verify_run_audit_trail(store, stored.run_id, require_checkpoints=True)
    assert report_empty.verified is False
    assert _check_map(report_empty)["checkpoint_chain"] is False

    t0 = datetime(2026, 7, 15, 10, 0, tzinfo=timezone.utc)
    store.save_checkpoint(
        RunCheckpoint(
            checkpoint_id="cp-1",
            run_id=stored.run_id,
            work_item_id="wi-1",
            stage=CheckpointStage.EIP_CREATED,
            fence_token=3,
            actor="planner-1",
            role="planner",
            created_at=t0,
        )
    )
    store.save_checkpoint(
        RunCheckpoint(
            checkpoint_id="cp-2",
            run_id=stored.run_id,
            work_item_id="wi-1",
            stage=CheckpointStage.VERIFIED,
            fence_token=2,  # regression: 3 -> 2
            actor="claude-verifier",
            role="verifier",
            created_at=t0 + timedelta(seconds=10),
        )
    )

    report_regress = verify_run_audit_trail(store, stored.run_id)
    assert report_regress.verified is False
    assert _check_map(report_regress)["checkpoint_chain"] is False
    assert any("fence_token regression" in f for f in report_regress.failures)


@pytest.mark.parametrize(
    "forbidden_key",
    ["approval_granted", "verification_override", "policy_override", "bypass_gate"],
)
def test_forbidden_authority_override_key_in_memory_events_fails(tmp_path: Path, forbidden_key: str):
    store = ServerStore(tmp_path / f"audit_mem_{forbidden_key}.sqlite3")
    evidence = _make_evidence(
        run_id=f"run-mem-{forbidden_key}",
        memory_events=[
            {"event": "memory_sync", "metadata": {forbidden_key: True}},
        ],
    )
    stored = store.save_run_evidence(evidence)
    store.save_release_decision(_make_release_decision(stored.run_id, stored.evidence_digest))
    _seed_checkpoints(store, stored.run_id)

    report = verify_run_audit_trail(store, stored.run_id)
    assert report.verified is False
    assert _check_map(report)["memory_authority"] is False
    assert any(forbidden_key in f for f in report.failures)


def test_missing_digests_and_failed_verification_fail(tmp_path: Path):
    store = ServerStore(tmp_path / "audit_digests.sqlite3")
    evidence = _make_evidence(
        run_id="run-bad-verify",
        eip_digest="",
        proof_digest="   ",
        verification_passed=False,
    )
    stored = store.save_run_evidence(evidence)
    store.save_release_decision(
        _make_release_decision(
            stored.run_id,
            stored.evidence_digest,
            can_release=False,
            outcome=ReleaseOutcome.DENIED_POLICY,
        )
    )
    _seed_checkpoints(store, stored.run_id)

    report = verify_run_audit_trail(store, stored.run_id)
    assert report.verified is False
    checks = _check_map(report)
    assert checks["eip_and_proof_plan_digests"] is False
    assert checks["verification_passed"] is False
    assert checks["release_decision"] is False
