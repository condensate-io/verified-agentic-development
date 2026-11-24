import json
import sqlite3
from datetime import datetime, timedelta, timezone

from vad.contracts.models import AssuranceLevel, RiskTier
from vad.control_plane.checkpoints import CheckpointStage, RunCheckpoint
from vad.control_plane.clients import ClientManifest
from vad.control_plane.idempotency import (
    IdempotencyRecord,
    IdempotencyStatus,
    compute_request_hash,
    evaluate_idempotency,
)
from vad.control_plane.leases import TaskLease, TaskLeaseAcquireRequest, TaskLeaseStatus
from vad.memory.contracts import LessonReviewStatus, ProposedRuleType
from vad.policy.autonomy_matrix import PolicyStage
from vad.release.decision import ReleaseDecision, ReleaseOutcome
from vad.server.db.store import (
    DefectLinkRecord,
    DeliveryOutcomeRecord,
    LessonPromotionRecord,
    RunRecord,
    SCHEMA_VERSION,
    ServerStore,
)


def _register_client(store: ServerStore, tmp_path, client_id: str = "codex-local") -> ClientManifest:
    manifest = ClientManifest(
        client_id=client_id,
        display_name="Codex Local",
        client_type="codex",
        version="1.0.0",
        connection_mode="mcp",
        supported_capabilities=("repo_read",),
        workspace_root=tmp_path,
        trust_state="trusted",
    )
    return store.register_client_manifest(manifest)


def test_fresh_migration_reaches_v12_and_is_idempotent(tmp_path):
    db_path = tmp_path / "fresh_v12.sqlite3"
    store = ServerStore(db_path)

    store.migrate()
    store.migrate()

    with sqlite3.connect(db_path) as conn:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        indexes = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'index'")
        }
        lease_cols = {
            row[1]
            for row in conn.execute("PRAGMA table_info(task_leases)").fetchall()
        }

    assert SCHEMA_VERSION == 12
    assert version == 12
    assert "fence_token" in lease_cols
    assert {
        "idempotency_records",
        "run_checkpoints",
        "release_decisions",
        "lesson_promotions",
        "delivery_outcomes",
        "defect_links",
    }.issubset(tables)
    assert "idx_run_checkpoints_run_created" in indexes


def test_upgrade_from_v11_preserves_existing_data_and_backfills_fence_token(tmp_path):
    db_path = tmp_path / "upgrade_v11.sqlite3"
    store = ServerStore(db_path)

    # Seed schema v1..v11 tables without v12 additions
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        store._migrate_001(conn)
        store._migrate_002(conn)
        store._migrate_003(conn)
        store._migrate_004(conn)
        store._migrate_005(conn)
        store._migrate_006(conn)
        store._migrate_007(conn)
        store._migrate_008(conn)
        store._migrate_009(conn)
        store._migrate_010(conn)
        store._migrate_011(conn)

        manifest = ClientManifest(
            client_id="codex-v11",
            display_name="Codex v11",
            client_type="codex",
            version="1.0.0",
            connection_mode="mcp",
            supported_capabilities=("repo_read",),
            workspace_root=tmp_path,
            trust_state="trusted",
        )
        conn.execute(
            """
            INSERT INTO client_manifests(
                client_id, display_name, client_type, connection_mode,
                trust_state, workspace_root, payload_json, registered_at
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                manifest.client_id,
                manifest.display_name,
                manifest.client_type.value,
                manifest.connection_mode.value,
                manifest.trust_state.value,
                str(manifest.workspace_root),
                json.dumps(manifest.model_dump(mode="json"), sort_keys=True),
                "2026-07-01T00:00:00+00:00",
            ),
        )

        legacy_lease_payload = {
            "task_id": "legacy-task",
            "run_id": "run-v11",
            "client_id": "codex-v11",
            "role": "builder",
            "actor": "legacy-builder",
            "acquired_at": "2026-07-01T00:00:00+00:00",
            "expires_at": "2026-07-01T01:00:00+00:00",
            "status": "active",
        }
        conn.execute(
            """
            INSERT INTO task_leases(
                task_id, run_id, client_id, role, actor, acquired_at, expires_at, status, payload_json
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "legacy-task",
                "run-v11",
                "codex-v11",
                "builder",
                "legacy-builder",
                "2026-07-01T00:00:00+00:00",
                "2026-07-01T01:00:00+00:00",
                "active",
                json.dumps(legacy_lease_payload, sort_keys=True),
            ),
        )

        legacy_run = RunRecord(run_id="run-v11", ticket_id="TICKET-11", repo="local/repo")
        conn.execute(
            """
            INSERT INTO runs(
                run_id, ticket_id, repo, kind, status, rca_approved, created_at, updated_at, payload_json
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                legacy_run.run_id,
                legacy_run.ticket_id,
                legacy_run.repo,
                legacy_run.kind,
                legacy_run.status,
                0,
                legacy_run.created_at,
                legacy_run.updated_at,
                json.dumps(legacy_run.model_dump(mode="json"), sort_keys=True),
            ),
        )
        conn.execute("PRAGMA user_version = 11")

    reopened = ServerStore(db_path)
    reopened.migrate()

    with sqlite3.connect(db_path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 12
        # Running _migrate_012 directly again must remain idempotent
        reopened._migrate_012(conn)

    loaded_run = reopened.load_run("run-v11")
    loaded_lease = reopened.load_task_lease("legacy-task")
    listed_leases = reopened.list_task_leases(client_id="codex-v11")

    assert loaded_run.ticket_id == "TICKET-11"
    assert loaded_lease.fence_token == 1
    assert listed_leases == [loaded_lease]
    assert reopened.next_fence_token("legacy-task") == 2


def test_fenced_task_lease_and_next_fence_token_round_trip(tmp_path):
    db_path = tmp_path / "fenced_leases.sqlite3"
    store = ServerStore(db_path)
    _register_client(store, tmp_path, "codex-local")
    _register_client(store, tmp_path, "claude-local")

    assert store.next_fence_token("task-build") == 1

    now = datetime(2026, 7, 10, 12, 0, tzinfo=timezone.utc)
    acquired_1 = store.acquire_task_lease(
        TaskLeaseAcquireRequest(
            task_id="task-build",
            run_id="run-fence",
            client_id="codex-local",
            role="builder",
            actor="builder-1",
            ttl_seconds=120,
        ),
        now=now,
    )
    assert acquired_1.fence_token == 1
    assert store.next_fence_token("task-build") == 2

    reopened = ServerStore(db_path)
    loaded_1 = reopened.load_task_lease("task-build")
    assert loaded_1 == acquired_1
    assert loaded_1.fence_token == 1

    fenced_3 = TaskLease(
        task_id="task-build",
        run_id="run-fence",
        client_id="claude-local",
        role="builder",
        actor="builder-2",
        acquired_at=now + timedelta(seconds=10),
        expires_at=now + timedelta(seconds=130),
        status=TaskLeaseStatus.ACTIVE,
        fence_token=3,
    )
    reopened.save_task_lease(fenced_3)

    reopened_2 = ServerStore(db_path)
    assert reopened_2.load_task_lease("task-build") == fenced_3
    assert reopened_2.list_task_leases(client_id="claude-local") == [fenced_3]
    assert reopened_2.next_fence_token("task-build") == 4

    acquired_4 = reopened_2.acquire_task_lease(
        TaskLeaseAcquireRequest(
            task_id="task-build",
            run_id="run-fence",
            client_id="codex-local",
            role="builder",
            actor="builder-1",
        ),
        now=now + timedelta(seconds=20),
    )
    assert acquired_4.fence_token == 4


def test_idempotency_record_round_trip_across_reopen(tmp_path):
    db_path = tmp_path / "idempotency.sqlite3"
    store = ServerStore(db_path)

    assert store.load_idempotency_record("missing-key") is None

    req_hash = compute_request_hash({"run_id": "run-1", "action": "promote"})
    record = IdempotencyRecord(
        idempotency_key="idem-key-1",
        route="POST http://127.0.0.1:8765/runs/run-1/promote",
        request_hash=req_hash,
        status_code=201,
        response_payload={"status": "accepted", "run_id": "run-1"},
        created_at=datetime(2026, 7, 10, 12, 30, tzinfo=timezone.utc),
    )
    store.save_idempotency_record(record)

    reopened = ServerStore(db_path)
    loaded = reopened.load_idempotency_record("idem-key-1")
    assert loaded == record
    assert (
        evaluate_idempotency(
            loaded,
            route="POST http://127.0.0.1:8765/runs/run-1/promote",
            request_hash=req_hash,
        )
        == IdempotencyStatus.REPLAY
    )
    assert (
        evaluate_idempotency(
            loaded,
            route="POST http://127.0.0.1:8765/runs/run-1/promote",
            request_hash=compute_request_hash({"run_id": "run-1", "action": "different"}),
        )
        == IdempotencyStatus.CONFLICT
    )


def test_run_checkpoint_and_latest_checkpoint_round_trip(tmp_path):
    db_path = tmp_path / "checkpoints.sqlite3"
    store = ServerStore(db_path)

    assert store.latest_checkpoint("run-cp") is None
    assert store.list_checkpoints("run-cp") == []

    t0 = datetime(2026, 7, 10, 13, 0, tzinfo=timezone.utc)
    cp1 = RunCheckpoint(
        checkpoint_id="cp-1",
        run_id="run-cp",
        work_item_id="wi-1",
        stage=CheckpointStage.EIP_CREATED,
        fence_token=1,
        actor="planner-1",
        role="planner",
        artifact_digests={"eip": "a" * 64},
        state_snapshot={"step": 1},
        created_at=t0,
    )
    cp2 = RunCheckpoint(
        checkpoint_id="cp-2",
        run_id="run-cp",
        work_item_id="wi-1",
        stage=CheckpointStage.VERIFIED,
        fence_token=2,
        actor="verifier-1",
        role="verifier",
        artifact_digests={"eip": "a" * 64, "verification": "b" * 64},
        state_snapshot={"step": 2, "passed": True},
        created_at=t0 + timedelta(seconds=30),
    )
    cp_other_wi = RunCheckpoint(
        checkpoint_id="cp-3",
        run_id="run-cp",
        work_item_id="wi-2",
        stage=CheckpointStage.PROOF_PLANNED,
        fence_token=1,
        actor="planner-1",
        role="planner",
        artifact_digests={"proof": "c" * 64},
        state_snapshot={"step": 1},
        created_at=t0 + timedelta(seconds=15),
    )

    store.save_checkpoint(cp1)
    store.save_checkpoint(cp2)
    store.save_checkpoint(cp_other_wi)

    reopened = ServerStore(db_path)
    all_for_run = reopened.list_checkpoints("run-cp")
    wi1_only = reopened.list_checkpoints("run-cp", work_item_id="wi-1")

    assert all_for_run == [cp1, cp_other_wi, cp2]
    assert wi1_only == [cp1, cp2]
    assert reopened.latest_checkpoint("run-cp") == cp2
    assert reopened.latest_checkpoint("run-cp", work_item_id="wi-2") == cp_other_wi


def test_release_decision_round_trip_across_reopen(tmp_path):
    db_path = tmp_path / "release_decisions.sqlite3"
    store = ServerStore(db_path)

    dec1 = ReleaseDecision(
        decision_id="dec-1",
        run_id="run-rel-1",
        eip_digest="a" * 64,
        evidence_digest="b" * 64,
        risk_tier=RiskTier.HIGH,
        stage=PolicyStage.DEPLOY,
        assurance_required=AssuranceLevel.A3_ATTESTED,
        assurance_achieved=AssuranceLevel.A3_ATTESTED,
        outcome=ReleaseOutcome.APPROVED_WITH_HUMAN,
        can_release=True,
        reasons=["two distinct human approvals recorded"],
        required_signoffs=2,
        recorded_approvers=["alice", "bob"],
        explicit_authorization_ref="auth-ref-42",
        decided_at=datetime(2026, 7, 10, 14, 0, tzinfo=timezone.utc),
    )
    dec2 = ReleaseDecision(
        decision_id="dec-2",
        run_id="run-rel-2",
        eip_digest="c" * 64,
        evidence_digest="d" * 64,
        risk_tier=RiskTier.LOW,
        stage=PolicyStage.BUILD,
        assurance_required=AssuranceLevel.A1_BASIC,
        assurance_achieved=AssuranceLevel.A1_BASIC,
        outcome=ReleaseOutcome.AUTONOMOUS_RELEASE,
        can_release=True,
        reasons=["low risk autonomous pass"],
        required_signoffs=0,
        decided_at=datetime(2026, 7, 10, 14, 5, tzinfo=timezone.utc),
    )

    store.save_release_decision(dec1)
    store.save_release_decision(dec2)

    reopened = ServerStore(db_path)
    assert reopened.list_release_decisions("run-rel-1") == [dec1]
    assert reopened.list_release_decisions() == [dec1, dec2]


def test_lesson_promotion_round_trip_and_filtering(tmp_path):
    db_path = tmp_path / "lessons.sqlite3"
    store = ServerStore(db_path)

    assert store.load_lesson_promotion("lesson-missing") is None

    proposed = LessonPromotionRecord(
        promotion_id="promo-1",
        lesson_id="lesson-1",
        title="Require migration idempotency check",
        recommendation="Always check PRAGMA table_info before ALTER TABLE.",
        proposed_rule_type=ProposedRuleType.INVARIANT,
        review_status=LessonReviewStatus.PROPOSED,
        derived_from_fact_ids=["fact-1", "fact-2"],
        domain_tags=["sqlite", "migrations"],
        confidence=0.85,
        updated_at="2026-07-10T15:00:00+00:00",
    )
    store.upsert_lesson_promotion(proposed)

    reopened = ServerStore(db_path)
    assert reopened.load_lesson_promotion("lesson-1") == proposed
    assert reopened.list_lesson_promotions(review_status="proposed") == [proposed]
    assert reopened.list_lesson_promotions(review_status="approved") == []

    approved = proposed.model_copy(
        update={
            "review_status": LessonReviewStatus.APPROVED.value,
            "reviewed_by": "guardian-alice",
            "reviewed_at": "2026-07-10T15:10:00+00:00",
            "updated_at": "2026-07-10T15:10:00+00:00",
        }
    )
    reopened.upsert_lesson_promotion(approved)

    reopened_2 = ServerStore(db_path)
    assert reopened_2.load_lesson_promotion("lesson-1") == approved
    assert reopened_2.list_lesson_promotions(review_status="proposed") == []
    assert reopened_2.list_lesson_promotions(review_status=LessonReviewStatus.APPROVED) == [approved]


def test_delivery_outcome_and_defect_link_round_trip(tmp_path):
    db_path = tmp_path / "outcomes_defects.sqlite3"
    store = ServerStore(db_path)

    out1 = DeliveryOutcomeRecord(
        outcome_id="out-1",
        run_id="run-m1",
        evidence_digest="e" * 64,
        risk_tier=RiskTier.MEDIUM,
        accepted=True,
        autonomous=True,
        verification_false_positive=False,
        fault_injected=True,
        recovered=True,
        human_review_seconds=None,
        cost_usd=0.42,
        created_at="2026-07-10T16:00:00+00:00",
    )
    out2 = DeliveryOutcomeRecord(
        outcome_id="out-2",
        run_id="run-m2",
        evidence_digest="f" * 64,
        risk_tier=RiskTier.HIGH,
        accepted=False,
        autonomous=False,
        verification_false_positive=True,
        fault_injected=False,
        recovered=False,
        human_review_seconds=185.5,
        cost_usd=1.25,
        created_at="2026-07-10T16:05:00+00:00",
    )
    defect1 = DefectLinkRecord(
        defect_id="def-1",
        run_id="run-m2",
        evidence_digest="f" * 64,
        incident_id="INC-2026-009",
        severity="high",
        summary="Escaped regression in retry backoff.",
        created_at="2026-07-10T16:10:00+00:00",
    )

    store.record_delivery_outcome(out1)
    store.record_delivery_outcome(out2)
    store.record_defect_link(defect1)

    reopened = ServerStore(db_path)
    assert reopened.list_delivery_outcomes() == [out1, out2]
    assert reopened.list_delivery_outcomes(run_id="run-m1") == [out1]
    assert reopened.list_delivery_outcomes(risk_tier="high") == [out2]
    assert reopened.list_defect_links() == [defect1]
    assert reopened.list_defect_links(run_id="run-m2") == [defect1]
    assert reopened.list_defect_links(incident_id="INC-2026-009") == [defect1]
    assert reopened.list_defect_links(run_id="run-m1") == []
