from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from vad.control_plane.checkpoints import (
    STAGE_ORDER,
    CheckpointStage,
    RunCheckpoint,
    next_resume_stage,
)
from vad.control_plane.idempotency import (
    IdempotencyRecord,
    IdempotencyStatus,
    compute_request_hash,
    evaluate_idempotency,
)
from vad.control_plane.sessions import (
    HostRole,
    HostSessionClaims,
    HostSessionManager,
    HostSessionToken,
)

_SECRET = b"vad-test-session-secret-0123456789"
_VALID_DIGEST_A = "a" * 64
_VALID_DIGEST_B = "b" * 64


# ---------------------------------------------------------------------------
# Idempotency tests
# ---------------------------------------------------------------------------


def test_compute_request_hash_is_canonical_and_deterministic():
    payload_a = {"work_item_id": "wi-1", "meta": {"b": 2, "a": 1}, "items": [1, 2]}
    payload_b = {"items": [1, 2], "meta": {"a": 1, "b": 2}, "work_item_id": "wi-1"}
    payload_c = {"work_item_id": "wi-2", "meta": {"a": 1, "b": 2}, "items": [1, 2]}

    hash_a = compute_request_hash(payload_a)
    hash_b = compute_request_hash(payload_b)
    hash_c = compute_request_hash(payload_c)

    assert len(hash_a) == 64
    assert hash_a == hash_b
    assert hash_a != hash_c


def test_idempotency_record_validation_and_utc_normalization():
    naive_time = datetime(2026, 11, 24, 1, 0, 0)
    offset_time = datetime(2026, 11, 24, 12, 0, 0, tzinfo=timezone(timedelta(hours=11)))

    rec1 = IdempotencyRecord(
        idempotency_key="idem-key-1",
        route="/work-items/wi-1/claim",
        request_hash=_VALID_DIGEST_A,
        status_code=200,
        response_payload={"ok": True},
        created_at=naive_time,
    )
    assert rec1.created_at.tzinfo == timezone.utc
    assert rec1.created_at == datetime(2026, 11, 24, 1, 0, 0, tzinfo=timezone.utc)

    rec2 = IdempotencyRecord(
        idempotency_key="idem-key-2",
        route="/work-items/wi-1/claim",
        request_hash=_VALID_DIGEST_A,
        status_code=201,
        response_payload={},
        created_at=offset_time,
    )
    assert rec2.created_at == datetime(2026, 11, 24, 1, 0, 0, tzinfo=timezone.utc)

    for bad_key in ["bad/key", "bad\\key", "bad\nkey", "bad\rkey", "bad\tkey", ""]:
        with pytest.raises(ValidationError):
            IdempotencyRecord(
                idempotency_key=bad_key,
                route="/work-items/wi-1/claim",
                request_hash=_VALID_DIGEST_A,
                status_code=200,
                response_payload={},
            )

    with pytest.raises(ValidationError):
        IdempotencyRecord(
            idempotency_key="ok-key",
            route="/bad\nroute",
            request_hash=_VALID_DIGEST_A,
            status_code=200,
            response_payload={},
        )

    with pytest.raises(ValidationError):
        IdempotencyRecord(
            idempotency_key="ok-key",
            route="/work-items/wi-1/claim",
            request_hash="not-a-sha256",
            status_code=200,
            response_payload={},
        )

    for bad_status in [99, 600]:
        with pytest.raises(ValidationError):
            IdempotencyRecord(
                idempotency_key="ok-key",
                route="/work-items/wi-1/claim",
                request_hash=_VALID_DIGEST_A,
                status_code=bad_status,
                response_payload={},
            )


def test_evaluate_idempotency_new_replay_and_conflict():
    req_hash = compute_request_hash({"work_item_id": "wi-1", "client_id": "codex-1"})

    assert (
        evaluate_idempotency(None, route="/work-items/wi-1/claim", request_hash=req_hash)
        == IdempotencyStatus.NEW
    )

    existing = IdempotencyRecord(
        idempotency_key="idem-1",
        route="/work-items/wi-1/claim",
        request_hash=req_hash,
        status_code=200,
        response_payload={"lease_id": "lease-1"},
    )

    assert (
        evaluate_idempotency(existing, route="/work-items/wi-1/claim", request_hash=req_hash)
        == IdempotencyStatus.REPLAY
    )
    assert (
        evaluate_idempotency(existing, route="/work-items/wi-1/release", request_hash=req_hash)
        == IdempotencyStatus.CONFLICT
    )
    assert (
        evaluate_idempotency(existing, route="/work-items/wi-1/claim", request_hash=_VALID_DIGEST_B)
        == IdempotencyStatus.CONFLICT
    )


# ---------------------------------------------------------------------------
# Checkpoint tests
# ---------------------------------------------------------------------------


def test_checkpoint_stage_order_and_next_resume_stage():
    expected_sequence = [
        CheckpointStage.EIP_CREATED,
        CheckpointStage.PROOF_PLANNED,
        CheckpointStage.CONSTRUCTION_ASSIGNED,
        CheckpointStage.DIFF_PROPOSED,
        CheckpointStage.VERIFIED,
        CheckpointStage.POLICY_DECIDED,
        CheckpointStage.RELEASED,
        CheckpointStage.MEMORY_SYNCED,
    ]
    assert [STAGE_ORDER[s] for s in expected_sequence] == list(range(1, 9))
    assert next_resume_stage(None) == CheckpointStage.EIP_CREATED

    for idx, stage in enumerate(expected_sequence):
        checkpoint = RunCheckpoint(
            checkpoint_id=f"cp-{idx + 1}",
            run_id="run-1",
            work_item_id="wi-1",
            stage=stage,
            fence_token=1,
            actor="orchestrator",
            role="operator",
        )
        expected_next = expected_sequence[idx + 1] if idx + 1 < len(expected_sequence) else None
        assert next_resume_stage(checkpoint) == expected_next


def test_run_checkpoint_validation_and_artifact_digests():
    cp = RunCheckpoint(
        checkpoint_id="cp-1",
        run_id="run-1",
        work_item_id="wi-1",
        stage=CheckpointStage.VERIFIED,
        fence_token=3,
        actor="claude-verifier",
        role="verifier",
        artifact_digests={"eip": _VALID_DIGEST_A, "verification_report": _VALID_DIGEST_B},
        state_snapshot={"passed": True},
        created_at=datetime(2026, 11, 24, 2, 0, 0),
    )
    assert cp.created_at.tzinfo == timezone.utc
    assert cp.fence_token == 3

    with pytest.raises(ValidationError):
        RunCheckpoint(
            checkpoint_id="cp/bad",
            run_id="run-1",
            work_item_id="wi-1",
            stage=CheckpointStage.EIP_CREATED,
            actor="builder",
            role="builder",
        )

    with pytest.raises(ValidationError):
        RunCheckpoint(
            checkpoint_id="cp-1",
            run_id="run-1",
            work_item_id="wi-1",
            stage=CheckpointStage.EIP_CREATED,
            fence_token=0,
            actor="builder",
            role="builder",
        )

    for bad_digest in ["short", "A" * 64, "z" * 64]:
        with pytest.raises(ValidationError):
            RunCheckpoint(
                checkpoint_id="cp-1",
                run_id="run-1",
                work_item_id="wi-1",
                stage=CheckpointStage.DIFF_PROPOSED,
                actor="builder",
                role="builder",
                artifact_digests={"diff": bad_digest},
            )


# ---------------------------------------------------------------------------
# Host session & role isolation tests
# ---------------------------------------------------------------------------


def _make_claims(
    *,
    role: HostRole = HostRole.BUILDER,
    work_item_id: str | None = "wi-100",
    fence_token: int = 2,
    issued_at: datetime | None = None,
    expires_at: datetime | None = None,
    workspace_root: str | None = "C:\\repos\\vad",
) -> HostSessionClaims:
    now = issued_at or datetime(2026, 11, 24, 1, 0, 0, tzinfo=timezone.utc)
    return HostSessionClaims(
        session_id="sess-1",
        client_id="codex-local",
        host_type="codex",
        role=role,
        actor="codex-builder",
        work_item_id=work_item_id,
        fence_token=fence_token,
        workspace_root=workspace_root,
        issued_at=now,
        expires_at=expires_at or (now + timedelta(minutes=15)),
    )


def test_session_manager_rejects_weak_secrets():
    with pytest.raises(ValueError):
        HostSessionManager(b"")
    with pytest.raises(ValueError):
        HostSessionManager(b"too-short-15-by")
    manager = HostSessionManager(b"0123456789abcdef")
    assert manager is not None


def test_session_claims_identifier_and_workspace_validation():
    claims = _make_claims(workspace_root="/tmp/workspaces/vad-run-1")
    assert claims.workspace_root == "/tmp/workspaces/vad-run-1"
    assert claims.issued_at.tzinfo == timezone.utc
    assert claims.expires_at.tzinfo == timezone.utc

    for field_name in ["session_id", "client_id", "host_type", "actor", "work_item_id"]:
        kwargs = {field_name: "bad/value"}
        with pytest.raises(ValidationError):
            HostSessionClaims(
                **{
                    **claims.model_dump(),
                    **kwargs,
                }
            )

    with pytest.raises(ValidationError):
        _make_claims(workspace_root="/bad/path\nnewline")


def test_session_issue_verify_and_tamper_rejection():
    manager = HostSessionManager(_SECRET)
    other_manager = HostSessionManager(b"different-secret-0123456789abcdef")
    now = datetime(2026, 11, 24, 1, 0, 0, tzinfo=timezone.utc)

    claims = _make_claims(issued_at=now, expires_at=now + timedelta(minutes=10))
    token = manager.issue(claims)

    # Signature should be base64url without padding or '+'/'/'
    assert "=" not in token.signature
    assert "+" not in token.signature
    assert "/" not in token.signature

    ok, err = manager.verify(
        token,
        now=now + timedelta(minutes=5),
        required_role=HostRole.BUILDER,
        expected_work_item_id="wi-100",
        min_fence_token=2,
    )
    assert ok is True
    assert err is None

    # Wrong signing secret
    ok, err = other_manager.verify(token, now=now)
    assert ok is False
    assert err is not None and "signature" in err

    # Tampered signature
    bad_sig_token = HostSessionToken(claims=token.claims, signature="tampered-sig")
    ok, err = manager.verify(bad_sig_token, now=now)
    assert ok is False
    assert err is not None and "signature" in err

    # Tampered claims (role escalation / fence bump / work item switch)
    escalated_claims = token.claims.model_copy(update={"role": HostRole.RELEASE_GUARDIAN})
    tampered_token = HostSessionToken(claims=escalated_claims, signature=token.signature)
    ok, err = manager.verify(tampered_token, now=now)
    assert ok is False
    assert err is not None and "signature" in err


def test_session_verify_expiry_role_work_item_and_fence_checks():
    manager = HostSessionManager(_SECRET)
    now = datetime(2026, 11, 24, 1, 0, 0, tzinfo=timezone.utc)
    claims = _make_claims(
        role=HostRole.BUILDER,
        work_item_id="wi-100",
        fence_token=3,
        issued_at=now,
        expires_at=now + timedelta(minutes=10),
    )
    token = manager.issue(claims)

    # Exact expiry boundary and past expiry
    ok, err = manager.verify(token, now=now + timedelta(minutes=10))
    assert ok is False
    assert err is not None and "expired" in err

    ok, err = manager.verify(token, now=now + timedelta(minutes=11))
    assert ok is False
    assert err is not None and "expired" in err

    # Role mismatch
    ok, err = manager.verify(token, now=now, required_role=HostRole.VERIFIER)
    assert ok is False
    assert err is not None and "role mismatch" in err

    # Work item mismatch
    ok, err = manager.verify(token, now=now, expected_work_item_id="wi-999")
    assert ok is False
    assert err is not None and "work_item_id mismatch" in err

    # Stale fence token
    ok, err = manager.verify(token, now=now, min_fence_token=4)
    assert ok is False
    assert err is not None and "stale fence token" in err


@pytest.mark.parametrize(
    ("role", "allowed_actions", "denied_actions"),
    [
        (
            HostRole.BUILDER,
            [
                "discover_work",
                "claim_work",
                "report_progress",
                "submit_diff_proposal",
                "submit_builder_claim",
                "escalate",
            ],
            [
                "submit_verification_result",
                "approve_release",
                "promote_lesson",
                "override_policy",
                "create_work",
            ],
        ),
        (
            HostRole.VERIFIER,
            [
                "discover_work",
                "claim_work",
                "report_progress",
                "submit_verification_result",
                "escalate",
            ],
            [
                "submit_diff_proposal",
                "approve_release",
                "promote_lesson",
                "mutate_workspace",
                "override_policy",
            ],
        ),
        (
            HostRole.RELEASE_GUARDIAN,
            [
                "approve_release",
                "reject_release",
                "promote_lesson",
                "inspect_audit",
            ],
            [
                "submit_diff_proposal",
                "submit_verification_result",
                "claim_work",
                "create_work",
            ],
        ),
        (
            HostRole.OPERATOR,
            [
                "create_work",
                "assign_work",
                "recover_stale",
                "inspect_audit",
                "approve_release",
                "promote_lesson",
            ],
            [
                "submit_diff_proposal",
                "submit_verification_result",
                "mutate_workspace",
            ],
        ),
    ],
)
def test_cross_role_action_isolation_matrix(
    role: HostRole,
    allowed_actions: list[str],
    denied_actions: list[str],
):
    manager = HostSessionManager(_SECRET)
    claims = _make_claims(role=role)

    for action in allowed_actions:
        ok, err = manager.assert_role_action_allowed(claims, action)
        assert ok is True, f"Expected {role.value} to allow {action}, got {err}"
        assert err is None
        assert HostSessionManager.assert_role_action_allowed(claims, action) == (True, None)

    for action in denied_actions:
        ok, err = manager.assert_role_action_allowed(claims, action)
        assert ok is False, f"Expected {role.value} to deny {action}"
        assert err is not None and action in err
