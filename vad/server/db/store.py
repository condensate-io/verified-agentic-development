from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from vad.evidence.bundle import EvidenceBundle, RunEvidence
from vad.policy.decisions import PolicyDecision
from vad.control_plane.clients import ClientHeartbeat, ClientManifest, ClientRuntimeStatus, ClientStatusSnapshot
from vad.control_plane.events import ControlPlaneEvent
from vad.control_plane.leases import TaskLease, TaskLeaseStatus
from vad.control_plane.projection import build_dashboard_projection
from vad.control_plane.governance_records import OperatorIntentRecord
from vad.control_plane.plugins import PluginInventoryRecord
from vad.control_plane.run_task_state import RunTaskState
from vad.control_plane.work_items import WorkItem, WorkItemStatus
from vad.repo.diff_workflow import DiffApplyRecord, DiffProposal


SCHEMA_VERSION = 10


class ApprovalEvent(BaseModel):
    approval_id: str
    run_id: str
    actor: str
    action: str
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    decision: PolicyDecision
    evidence_digest: str | None = None


class StoredRunEvidence(BaseModel):
    run_id: str
    evidence_digest: str
    evidence: RunEvidence


class DashboardActivity(BaseModel):
    activity_id: str
    run_id: str
    kind: str
    status: str
    client: str
    actor: str
    role: str
    summary: str
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    task_id: str | None = None
    evidence_digest: str | None = None
    policy_decision: PolicyDecision | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class ProofStreamRecord(BaseModel):
    record_id: str
    run_id: str
    task_id: str
    status: str
    client: str
    actor: str
    role: str
    summary: str
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    client_id: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    evidence_digest: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    recovery_event_id: str | None = None
    recovery_evidence_url: str | None = None


class TerminalStreamRecord(BaseModel):
    record_id: str
    run_id: str
    task_id: str
    kind: str
    status: str
    client: str
    role: str
    summary: str
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    event_id: str | None = None
    evidence_digest: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")


class ServerStore:
    def __init__(self, db_path: Path):
        self.db_path = db_path

    def migrate(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute("PRAGMA foreign_keys = ON")
            current = self._schema_version(conn)
            if current > SCHEMA_VERSION:
                raise RuntimeError(f"database schema {current} is newer than supported {SCHEMA_VERSION}")
            if current == 0:
                self._migrate_001(conn)
                current = 1
            if current < 2:
                self._migrate_002(conn)
                current = 2
            if current < 3:
                self._migrate_003(conn)
                current = 3
            if current < 4:
                self._migrate_004(conn)
                current = 4
            if current < 5:
                self._migrate_005(conn)
                current = 5
            if current < 6:
                self._migrate_006(conn)
                current = 6
            if current < 7:
                self._migrate_007(conn)
                current = 7
            if current < 8:
                self._migrate_008(conn)
                current = 8
            if current < 9:
                self._migrate_009(conn)
                current = 9
            if current < 10:
                self._migrate_010(conn)
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    def save_run_evidence(self, evidence: RunEvidence) -> StoredRunEvidence:
        self.migrate()
        digest = EvidenceBundle(evidence).compute_hash()
        payload = json.dumps(evidence.model_dump(mode="json"), sort_keys=True)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO run_evidence(run_id, evidence_digest, payload_json, created_at, final_decision)
                VALUES(?, ?, ?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                    evidence_digest=excluded.evidence_digest,
                    payload_json=excluded.payload_json,
                    created_at=excluded.created_at,
                    final_decision=excluded.final_decision
                """,
                (evidence.run_id, digest, payload, evidence.created_at, evidence.final_decision),
            )
        return StoredRunEvidence(run_id=evidence.run_id, evidence_digest=digest, evidence=evidence)

    def load_run_evidence(self, run_id: str) -> StoredRunEvidence:
        self.migrate()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT evidence_digest, payload_json FROM run_evidence WHERE run_id = ?",
                (run_id,),
            ).fetchone()
        if row is None:
            raise KeyError(run_id)
        evidence = RunEvidence(**json.loads(row["payload_json"]))
        digest = EvidenceBundle(evidence).compute_hash()
        if digest != row["evidence_digest"]:
            raise ValueError("stored run evidence digest mismatch")
        return StoredRunEvidence(run_id=run_id, evidence_digest=digest, evidence=evidence)

    def list_run_evidence(self) -> list[StoredRunEvidence]:
        self.migrate()
        with self._connect() as conn:
            rows = conn.execute("SELECT run_id FROM run_evidence ORDER BY created_at, run_id").fetchall()
        return [self.load_run_evidence(row["run_id"]) for row in rows]

    def save_approval_event(self, event: ApprovalEvent) -> ApprovalEvent:
        self.migrate()
        payload = json.dumps(event.model_dump(mode="json"), sort_keys=True)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO approval_events(
                    approval_id, run_id, actor, action, created_at, decision_json, evidence_digest
                )
                VALUES(?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.approval_id,
                    event.run_id,
                    event.actor,
                    event.action,
                    event.created_at,
                    payload,
                    event.evidence_digest,
                ),
            )
        return event

    def list_approval_events(self, run_id: str | None = None) -> list[ApprovalEvent]:
        self.migrate()
        query = "SELECT decision_json FROM approval_events"
        params: tuple[Any, ...] = ()
        if run_id is not None:
            query += " WHERE run_id = ?"
            params = (run_id,)
        query += " ORDER BY created_at, approval_id"
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [ApprovalEvent(**json.loads(row["decision_json"])) for row in rows]

    def save_dashboard_activity(self, activity: DashboardActivity) -> DashboardActivity:
        self.migrate()
        payload = json.dumps(activity.model_dump(mode="json"), sort_keys=True)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO dashboard_activity(
                    activity_id, run_id, kind, status, client, actor, role, summary,
                    created_at, task_id, evidence_digest, payload_json
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(activity_id) DO UPDATE SET
                    run_id=excluded.run_id,
                    kind=excluded.kind,
                    status=excluded.status,
                    client=excluded.client,
                    actor=excluded.actor,
                    role=excluded.role,
                    summary=excluded.summary,
                    created_at=excluded.created_at,
                    task_id=excluded.task_id,
                    evidence_digest=excluded.evidence_digest,
                    payload_json=excluded.payload_json
                """,
                (
                    activity.activity_id,
                    activity.run_id,
                    activity.kind,
                    activity.status,
                    activity.client,
                    activity.actor,
                    activity.role,
                    activity.summary,
                    activity.created_at,
                    activity.task_id,
                    activity.evidence_digest,
                    payload,
                ),
            )
        return activity

    def list_dashboard_activity(self, run_id: str | None = None) -> list[DashboardActivity]:
        self.migrate()
        query = "SELECT payload_json FROM dashboard_activity"
        params: tuple[Any, ...] = ()
        if run_id is not None:
            query += " WHERE run_id = ?"
            params = (run_id,)
        query += " ORDER BY created_at, activity_id"
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [DashboardActivity(**json.loads(row["payload_json"])) for row in rows]

    def save_proof_stream_record(self, record: ProofStreamRecord) -> ProofStreamRecord:
        self.migrate()
        payload = json.dumps(record.model_dump(mode="json"), sort_keys=True)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO proof_stream_records(
                    record_id, run_id, task_id, status, created_at, evidence_digest, payload_json
                )
                VALUES(?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(record_id) DO UPDATE SET
                    run_id=excluded.run_id,
                    task_id=excluded.task_id,
                    status=excluded.status,
                    created_at=excluded.created_at,
                    evidence_digest=excluded.evidence_digest,
                    payload_json=excluded.payload_json
                """,
                (
                    record.record_id,
                    record.run_id,
                    record.task_id,
                    record.status,
                    record.created_at,
                    record.evidence_digest,
                    payload,
                ),
            )
        return record

    def list_proof_stream_records(self, run_id: str | None = None) -> list[ProofStreamRecord]:
        self.migrate()
        query = "SELECT payload_json FROM proof_stream_records"
        params: tuple[Any, ...] = ()
        if run_id is not None:
            query += " WHERE run_id = ?"
            params = (run_id,)
        query += " ORDER BY created_at, record_id"
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [ProofStreamRecord(**json.loads(row["payload_json"])) for row in rows]

    def save_terminal_stream_record(self, record: TerminalStreamRecord) -> TerminalStreamRecord:
        self.migrate()
        payload = json.dumps(record.model_dump(mode="json"), sort_keys=True)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO terminal_stream_records(
                    record_id, run_id, task_id, kind, status, created_at, evidence_digest, payload_json
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(record_id) DO UPDATE SET
                    run_id=excluded.run_id,
                    task_id=excluded.task_id,
                    kind=excluded.kind,
                    status=excluded.status,
                    created_at=excluded.created_at,
                    evidence_digest=excluded.evidence_digest,
                    payload_json=excluded.payload_json
                """,
                (
                    record.record_id,
                    record.run_id,
                    record.task_id,
                    record.kind,
                    record.status,
                    record.created_at,
                    record.evidence_digest,
                    payload,
                ),
            )
        return record

    def list_terminal_stream_records(self, run_id: str | None = None) -> list[TerminalStreamRecord]:
        self.migrate()
        query = "SELECT payload_json FROM terminal_stream_records"
        params: tuple[Any, ...] = ()
        if run_id is not None:
            query += " WHERE run_id = ?"
            params = (run_id,)
        query += " ORDER BY created_at, record_id"
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [TerminalStreamRecord(**json.loads(row["payload_json"])) for row in rows]

    def dashboard_snapshot(self) -> dict[str, Any]:
        runs = self.list_run_evidence()
        activities = self.list_dashboard_activity()
        status_counts: dict[str, int] = {}
        kind_counts: dict[str, int] = {}
        client_counts: dict[str, int] = {}
        for activity in activities:
            status_counts[activity.status] = status_counts.get(activity.status, 0) + 1
            kind_counts[activity.kind] = kind_counts.get(activity.kind, 0) + 1
            client_counts[activity.client] = client_counts.get(activity.client, 0) + 1
        return {
            "runs": [
                {
                    "run_id": run.run_id,
                    "final_decision": run.evidence.final_decision,
                    "evidence_digest": run.evidence_digest,
                }
                for run in runs
            ],
            "activity": [activity.model_dump(mode="json") for activity in activities],
            "event_timeline": [activity.model_dump(mode="json") for activity in activities],
            "status_counts": status_counts,
            "kind_counts": kind_counts,
            "client_counts": client_counts,
        }

    def control_plane_dashboard_snapshot(
        self,
        *,
        run_id: str | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        events = self.list_control_plane_events(run_id=run_id)
        projection = build_dashboard_projection(events, now=now)
        runs = self.list_run_evidence()
        if run_id is not None:
            runs = [run for run in runs if run.run_id == run_id]
        projection["runs"] = [
            {
                "run_id": run.run_id,
                "final_decision": run.evidence.final_decision,
                "evidence_digest": run.evidence_digest,
            }
            for run in runs
        ]
        projection["replay"] = {
            "mode": "event_ledger",
            "run_id": run_id,
            "event_count": len(events),
            "source": "control_plane_events",
        }
        return projection

    def replay_dashboard_snapshot(
        self,
        *,
        run_id: str | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        return self.control_plane_dashboard_snapshot(run_id=run_id, now=now)

    def has_control_plane_events(self) -> bool:
        self.migrate()
        with self._connect() as conn:
            row = conn.execute("SELECT 1 FROM control_plane_events LIMIT 1").fetchone()
        return row is not None

    def register_client_manifest(self, manifest: ClientManifest) -> ClientManifest:
        self.migrate()
        payload = json.dumps(manifest.model_dump(mode="json"), sort_keys=True)
        with self._connect() as conn:
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
                    payload,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
        return manifest

    def list_client_manifests(self) -> list[ClientManifest]:
        self.migrate()
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT payload_json FROM client_manifests ORDER BY display_name, client_id"
            ).fetchall()
        return [ClientManifest(**json.loads(row["payload_json"])) for row in rows]

    def load_client_manifest(self, client_id: str) -> ClientManifest:
        self.migrate()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT payload_json FROM client_manifests WHERE client_id = ?",
                (client_id,),
            ).fetchone()
        if row is None:
            raise KeyError(client_id)
        return ClientManifest(**json.loads(row["payload_json"]))

    def unregister_client_manifest(self, client_id: str) -> ClientManifest:
        manifest = self.load_client_manifest(client_id)
        with self._connect() as conn:
            conn.execute("DELETE FROM client_manifests WHERE client_id = ?", (client_id,))
        return manifest

    def record_client_heartbeat(self, heartbeat: ClientHeartbeat) -> ClientHeartbeat:
        self.load_client_manifest(heartbeat.client_id)
        self.migrate()
        payload = json.dumps(heartbeat.model_dump(mode="json"), sort_keys=True)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO client_heartbeats(
                    client_id, status, last_heartbeat_at, last_run_id, last_task_id,
                    lost_task_leases_json, payload_json
                )
                VALUES(?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(client_id) DO UPDATE SET
                    status=excluded.status,
                    last_heartbeat_at=excluded.last_heartbeat_at,
                    last_run_id=excluded.last_run_id,
                    last_task_id=excluded.last_task_id,
                    payload_json=excluded.payload_json
                """,
                (
                    heartbeat.client_id,
                    heartbeat.status.value,
                    heartbeat.observed_at.isoformat(),
                    heartbeat.run_id,
                    heartbeat.task_id,
                    json.dumps([]),
                    payload,
                ),
            )
        return heartbeat

    def list_client_statuses(self) -> list[ClientStatusSnapshot]:
        self.migrate()
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT cm.payload_json AS manifest_json,
                       ch.status,
                       ch.last_heartbeat_at,
                       ch.last_run_id,
                       ch.last_task_id,
                       ch.lost_task_leases_json
                FROM client_manifests cm
                LEFT JOIN client_heartbeats ch ON ch.client_id = cm.client_id
                ORDER BY cm.display_name, cm.client_id
                """
            ).fetchall()
        snapshots = []
        for row in rows:
            snapshots.append(ClientStatusSnapshot(
                manifest=ClientManifest(**json.loads(row["manifest_json"])),
                status=ClientRuntimeStatus(row["status"]) if row["status"] else ClientRuntimeStatus.DISCONNECTED,
                last_heartbeat_at=datetime.fromisoformat(row["last_heartbeat_at"]) if row["last_heartbeat_at"] else None,
                last_run_id=row["last_run_id"],
                last_task_id=row["last_task_id"],
                lost_task_leases=tuple(json.loads(row["lost_task_leases_json"] or "[]")),
            ))
        return snapshots

    def mark_client_stale(self, client_id: str, *, lost_task_leases: tuple[str, ...] | None = None) -> ClientStatusSnapshot:
        manifest = self.load_client_manifest(client_id)
        self.migrate()
        released_leases = lost_task_leases if lost_task_leases is not None else tuple(
            lease.task_id for lease in self.expire_task_leases_for_client(client_id)
        )
        with self._connect() as conn:
            row = conn.execute(
                "SELECT last_heartbeat_at, last_run_id, last_task_id FROM client_heartbeats WHERE client_id = ?",
                (client_id,),
            ).fetchone()
            conn.execute(
                """
                INSERT INTO client_heartbeats(
                    client_id, status, last_heartbeat_at, last_run_id, last_task_id,
                    lost_task_leases_json, payload_json
                )
                VALUES(?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(client_id) DO UPDATE SET
                    status=excluded.status,
                    lost_task_leases_json=excluded.lost_task_leases_json
                """,
                (
                    client_id,
                    ClientRuntimeStatus.STALE.value,
                    row["last_heartbeat_at"] if row else None,
                    row["last_run_id"] if row else None,
                    row["last_task_id"] if row else None,
                    json.dumps(list(released_leases)),
                    "{}",
                ),
            )
        return ClientStatusSnapshot(
            manifest=manifest,
            status=ClientRuntimeStatus.STALE,
            last_heartbeat_at=datetime.fromisoformat(row["last_heartbeat_at"]) if row and row["last_heartbeat_at"] else None,
            last_run_id=row["last_run_id"] if row else None,
            last_task_id=row["last_task_id"] if row else None,
            lost_task_leases=released_leases,
        )

    def save_task_lease(self, lease: TaskLease) -> TaskLease:
        self.load_client_manifest(lease.client_id)
        self.migrate()
        payload = json.dumps(lease.model_dump(mode="json"), sort_keys=True)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO task_leases(
                    task_id, run_id, client_id, role, actor, acquired_at,
                    expires_at, status, payload_json
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(task_id) DO UPDATE SET
                    run_id=excluded.run_id,
                    client_id=excluded.client_id,
                    role=excluded.role,
                    actor=excluded.actor,
                    acquired_at=excluded.acquired_at,
                    expires_at=excluded.expires_at,
                    status=excluded.status,
                    payload_json=excluded.payload_json
                """,
                (
                    lease.task_id,
                    lease.run_id,
                    lease.client_id,
                    lease.role,
                    lease.actor,
                    lease.acquired_at.isoformat(),
                    lease.expires_at.isoformat(),
                    lease.status.value,
                    payload,
                ),
            )
        return lease

    def load_task_lease(self, task_id: str) -> TaskLease:
        self.migrate()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT payload_json FROM task_leases WHERE task_id = ?",
                (task_id,),
            ).fetchone()
        if row is None:
            raise KeyError(task_id)
        return TaskLease(**json.loads(row["payload_json"]))

    def list_task_leases(
        self,
        *,
        client_id: str | None = None,
        status: TaskLeaseStatus | None = None,
    ) -> list[TaskLease]:
        self.migrate()
        query = "SELECT payload_json FROM task_leases"
        clauses = []
        params: list[Any] = []
        if client_id is not None:
            clauses.append("client_id = ?")
            params.append(client_id)
        if status is not None:
            clauses.append("status = ?")
            params.append(status.value)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY run_id, task_id"
        with self._connect() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
        return [TaskLease(**json.loads(row["payload_json"])) for row in rows]

    def expire_task_leases_for_client(self, client_id: str) -> list[TaskLease]:
        expired = []
        for lease in self.list_task_leases(client_id=client_id, status=TaskLeaseStatus.ACTIVE):
            lease.status = TaskLeaseStatus.EXPIRED
            lease.release_reason = "client marked stale"
            self.save_task_lease(lease)
            expired.append(lease)
        return expired

    def save_work_item(self, item: WorkItem) -> WorkItem:
        self.migrate()
        payload = json.dumps(item.model_dump(mode="json"), sort_keys=True)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO work_items(
                    work_item_id, run_id, title, role, requested_capability,
                    priority, status, created_at, updated_at, assigned_client_id,
                    lease_id, evidence_digest, payload_json
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item.work_item_id,
                    item.run_id,
                    item.title,
                    item.role,
                    item.requested_capability,
                    item.priority,
                    item.status.value,
                    item.created_at.isoformat(),
                    item.updated_at.isoformat(),
                    item.assigned_client_id,
                    item.lease_id,
                    item.evidence_digest,
                    payload,
                ),
            )
        self.upsert_run_task_state(RunTaskState.from_work_item(item))
        return item

    def update_work_item(self, item: WorkItem) -> WorkItem:
        self.migrate()
        self.load_work_item(item.work_item_id)
        payload = json.dumps(item.model_dump(mode="json"), sort_keys=True)
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE work_items
                SET run_id = ?,
                    title = ?,
                    role = ?,
                    requested_capability = ?,
                    priority = ?,
                    status = ?,
                    created_at = ?,
                    updated_at = ?,
                    assigned_client_id = ?,
                    lease_id = ?,
                    evidence_digest = ?,
                    payload_json = ?
                WHERE work_item_id = ?
                """,
                (
                    item.run_id,
                    item.title,
                    item.role,
                    item.requested_capability,
                    item.priority,
                    item.status.value,
                    item.created_at.isoformat(),
                    item.updated_at.isoformat(),
                    item.assigned_client_id,
                    item.lease_id,
                    item.evidence_digest,
                    payload,
                    item.work_item_id,
                ),
            )
        self.upsert_run_task_state(RunTaskState.from_work_item(item))
        return item

    def load_work_item(self, work_item_id: str) -> WorkItem:
        self.migrate()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT payload_json FROM work_items WHERE work_item_id = ?",
                (work_item_id,),
            ).fetchone()
        if row is None:
            raise KeyError(work_item_id)
        return WorkItem(**json.loads(row["payload_json"]))

    def list_work_items(
        self,
        *,
        run_id: str | None = None,
        status: WorkItemStatus | None = None,
        assigned_client_id: str | None = None,
    ) -> list[WorkItem]:
        self.migrate()
        query = "SELECT payload_json FROM work_items"
        clauses = []
        params: list[Any] = []
        if run_id is not None:
            clauses.append("run_id = ?")
            params.append(run_id)
        if status is not None:
            clauses.append("status = ?")
            params.append(status.value)
        if assigned_client_id is not None:
            clauses.append("assigned_client_id = ?")
            params.append(assigned_client_id)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY priority ASC, created_at ASC, work_item_id ASC"
        with self._connect() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
        return [WorkItem(**json.loads(row["payload_json"])) for row in rows]

    def save_plugin_inventory_record(self, record: PluginInventoryRecord) -> PluginInventoryRecord:
        self.migrate()
        payload = json.dumps(record.model_dump(mode="json"), sort_keys=True)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO plugin_inventory(
                    plugin_id, target_client, version, review_state,
                    dashboard_status, uninstall_status, rollback_status,
                    payload_json
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(plugin_id) DO UPDATE SET
                    target_client=excluded.target_client,
                    version=excluded.version,
                    review_state=excluded.review_state,
                    dashboard_status=excluded.dashboard_status,
                    uninstall_status=excluded.uninstall_status,
                    rollback_status=excluded.rollback_status,
                    payload_json=excluded.payload_json
                """,
                (
                    record.plugin_id,
                    record.target_client.value,
                    record.version,
                    record.review_state.value,
                    record.dashboard_status.value,
                    record.uninstall_status,
                    record.rollback_status,
                    payload,
                ),
            )
        return record

    def load_plugin_inventory_record(self, plugin_id: str) -> PluginInventoryRecord:
        self.migrate()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT payload_json FROM plugin_inventory WHERE plugin_id = ?",
                (plugin_id,),
            ).fetchone()
        if row is None:
            raise KeyError(plugin_id)
        return PluginInventoryRecord(**json.loads(row["payload_json"]))

    def list_plugin_inventory_records(
        self,
        *,
        target_client: str | None = None,
        review_state: str | None = None,
    ) -> list[PluginInventoryRecord]:
        self.migrate()
        query = "SELECT payload_json FROM plugin_inventory"
        clauses = []
        params: list[Any] = []
        if target_client is not None:
            clauses.append("target_client = ?")
            params.append(target_client)
        if review_state is not None:
            clauses.append("review_state = ?")
            params.append(review_state)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY target_client, plugin_id"
        with self._connect() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
        return [PluginInventoryRecord(**json.loads(row["payload_json"])) for row in rows]

    def save_diff_proposal(self, proposal: DiffProposal) -> DiffProposal:
        self.migrate()
        payload = json.dumps(proposal.model_dump(mode="json"), sort_keys=True)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO diff_proposals(
                    proposal_id, run_id, task_id, status, created_at, payload_json
                )
                VALUES(?, ?, ?, ?, ?, ?)
                ON CONFLICT(proposal_id) DO UPDATE SET
                    run_id=excluded.run_id,
                    task_id=excluded.task_id,
                    status=excluded.status,
                    payload_json=excluded.payload_json
                """,
                (
                    proposal.proposal_id,
                    proposal.run_id,
                    proposal.task_id,
                    "proposed",
                    proposal.created_at,
                    payload,
                ),
            )
        return proposal

    def load_diff_proposal(self, proposal_id: str) -> DiffProposal:
        self.migrate()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT payload_json FROM diff_proposals WHERE proposal_id = ?",
                (proposal_id,),
            ).fetchone()
        if row is None:
            raise KeyError(proposal_id)
        return DiffProposal(**json.loads(row["payload_json"]))

    def list_diff_proposals(
        self,
        *,
        run_id: str | None = None,
        task_id: str | None = None,
    ) -> list[DiffProposal]:
        self.migrate()
        query = "SELECT payload_json FROM diff_proposals"
        clauses = []
        params: list[Any] = []
        if run_id is not None:
            clauses.append("run_id = ?")
            params.append(run_id)
        if task_id is not None:
            clauses.append("task_id = ?")
            params.append(task_id)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY created_at ASC, proposal_id ASC"
        with self._connect() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
        return [DiffProposal(**json.loads(row["payload_json"])) for row in rows]

    def update_diff_proposal_status(self, proposal_id: str, *, status: str) -> DiffProposal:
        proposal = self.load_diff_proposal(proposal_id)
        with self._connect() as conn:
            conn.execute(
                "UPDATE diff_proposals SET status = ? WHERE proposal_id = ?",
                (status, proposal_id),
            )
        return proposal

    def save_diff_apply_record(self, record: DiffApplyRecord) -> DiffApplyRecord:
        self.migrate()
        payload = json.dumps(record.model_dump(mode="json"), sort_keys=True)
        record_id = f"{record.proposal_id}-apply-{record.created_at}"
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO diff_apply_records(
                    record_id, proposal_id, created_at, payload_json
                )
                VALUES(?, ?, ?, ?)
                """,
                (record_id, record.proposal_id, record.created_at, payload),
            )
        return record

    def list_diff_apply_records(self, *, proposal_id: str | None = None) -> list[DiffApplyRecord]:
        self.migrate()
        query = "SELECT payload_json FROM diff_apply_records"
        params: list[Any] = []
        if proposal_id is not None:
            query += " WHERE proposal_id = ?"
            params.append(proposal_id)
        query += " ORDER BY created_at ASC, record_id ASC"
        with self._connect() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
        return [DiffApplyRecord(**json.loads(row["payload_json"])) for row in rows]

    def upsert_run_task_state(self, state: RunTaskState) -> RunTaskState:
        self.migrate()
        payload = json.dumps(state.model_dump(mode="json"), sort_keys=True)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO run_task_states(
                    run_id, task_id, status, work_item_id, updated_at, payload_json
                )
                VALUES(?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id, task_id) DO UPDATE SET
                    status=excluded.status,
                    work_item_id=excluded.work_item_id,
                    updated_at=excluded.updated_at,
                    payload_json=excluded.payload_json
                """,
                (
                    state.run_id,
                    state.task_id,
                    state.status.value,
                    state.work_item_id,
                    state.updated_at.isoformat(),
                    payload,
                ),
            )
        return state

    def load_run_task_state(self, run_id: str, task_id: str) -> RunTaskState:
        self.migrate()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT payload_json FROM run_task_states WHERE run_id = ? AND task_id = ?",
                (run_id, task_id),
            ).fetchone()
        if row is None:
            raise KeyError((run_id, task_id))
        return RunTaskState(**json.loads(row["payload_json"]))

    def list_run_task_states(self, *, run_id: str | None = None) -> list[RunTaskState]:
        self.migrate()
        query = "SELECT payload_json FROM run_task_states"
        params: list[Any] = []
        if run_id is not None:
            query += " WHERE run_id = ?"
            params.append(run_id)
        query += " ORDER BY run_id ASC, task_id ASC"
        with self._connect() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
        return [RunTaskState(**json.loads(row["payload_json"])) for row in rows]

    def save_operator_intent_record(self, record: OperatorIntentRecord) -> OperatorIntentRecord:
        self.migrate()
        payload = json.dumps(record.model_dump(mode="json"), sort_keys=True)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO operator_intent_records(
                    intent_ref, actor, role, scope, created_at, payload_json
                )
                VALUES(?, ?, ?, ?, ?, ?)
                ON CONFLICT(intent_ref) DO UPDATE SET
                    actor=excluded.actor,
                    role=excluded.role,
                    scope=excluded.scope,
                    payload_json=excluded.payload_json
                """,
                (
                    record.intent_ref,
                    record.actor,
                    record.role,
                    record.scope,
                    record.created_at.isoformat(),
                    payload,
                ),
            )
        return record

    def load_operator_intent_record(self, intent_ref: str) -> OperatorIntentRecord:
        self.migrate()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT payload_json FROM operator_intent_records WHERE intent_ref = ?",
                (intent_ref,),
            ).fetchone()
        if row is None:
            raise KeyError(intent_ref)
        return OperatorIntentRecord(**json.loads(row["payload_json"]))

    def list_operator_intent_records(self) -> list[OperatorIntentRecord]:
        self.migrate()
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT payload_json FROM operator_intent_records ORDER BY created_at ASC, intent_ref ASC"
            ).fetchall()
        return [OperatorIntentRecord(**json.loads(row["payload_json"])) for row in rows]

    def append_control_plane_event(self, event: ControlPlaneEvent) -> ControlPlaneEvent:
        self.migrate()
        payload = json.dumps(event.model_dump(mode="json"), sort_keys=True)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO control_plane_events(
                    event_id, sequence, created_at, client_id, run_id, task_id,
                    kind, status, actor, role, evidence_digest, payload_json
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.event_id,
                    event.sequence,
                    event.created_at.isoformat(),
                    event.client_id,
                    event.run_id,
                    event.task_id,
                    event.kind.value,
                    event.status.value,
                    event.actor,
                    event.role,
                    event.evidence_digest,
                    payload,
                ),
            )
        return event

    def list_control_plane_events(
        self,
        *,
        run_id: str | None = None,
        client_id: str | None = None,
    ) -> list[ControlPlaneEvent]:
        self.migrate()
        query = "SELECT payload_json FROM control_plane_events"
        clauses = []
        params: list[Any] = []
        if run_id is not None:
            clauses.append("run_id = ?")
            params.append(run_id)
        if client_id is not None:
            clauses.append("client_id = ?")
            params.append(client_id)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY sequence, created_at, event_id"
        with self._connect() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
        return [ControlPlaneEvent(**json.loads(row["payload_json"])) for row in rows]

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _schema_version(self, conn: sqlite3.Connection) -> int:
        row = conn.execute("PRAGMA user_version").fetchone()
        return int(row[0])

    def _migrate_001(self, conn: sqlite3.Connection) -> None:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS run_evidence (
                run_id TEXT PRIMARY KEY,
                evidence_digest TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                final_decision TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS approval_events (
                approval_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                actor TEXT NOT NULL,
                action TEXT NOT NULL,
                created_at TEXT NOT NULL,
                decision_json TEXT NOT NULL,
                evidence_digest TEXT,
                FOREIGN KEY(run_id) REFERENCES run_evidence(run_id)
            );
            """
        )

    def _migrate_004(self, conn: sqlite3.Connection) -> None:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS client_manifests (
                client_id TEXT PRIMARY KEY,
                display_name TEXT NOT NULL,
                client_type TEXT NOT NULL,
                connection_mode TEXT NOT NULL,
                trust_state TEXT NOT NULL,
                workspace_root TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                registered_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_client_manifests_type
                ON client_manifests(client_type, display_name);
            CREATE INDEX IF NOT EXISTS idx_client_manifests_trust
                ON client_manifests(trust_state, display_name);
            """
        )

    def _migrate_005(self, conn: sqlite3.Connection) -> None:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS client_heartbeats (
                client_id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                last_heartbeat_at TEXT,
                last_run_id TEXT,
                last_task_id TEXT,
                lost_task_leases_json TEXT NOT NULL DEFAULT '[]',
                payload_json TEXT NOT NULL,
                FOREIGN KEY(client_id) REFERENCES client_manifests(client_id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_client_heartbeats_status
                ON client_heartbeats(status, last_heartbeat_at);
            """
        )

    def _migrate_006(self, conn: sqlite3.Connection) -> None:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS task_leases (
                task_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                client_id TEXT NOT NULL,
                role TEXT NOT NULL,
                actor TEXT NOT NULL,
                acquired_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                status TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                FOREIGN KEY(client_id) REFERENCES client_manifests(client_id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_task_leases_client_status
                ON task_leases(client_id, status, expires_at);
            CREATE INDEX IF NOT EXISTS idx_task_leases_run
                ON task_leases(run_id, status, task_id);
            """
        )

    def _migrate_007(self, conn: sqlite3.Connection) -> None:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS work_items (
                work_item_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                title TEXT NOT NULL,
                role TEXT NOT NULL,
                requested_capability TEXT,
                priority INTEGER NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                assigned_client_id TEXT,
                lease_id TEXT,
                evidence_digest TEXT,
                payload_json TEXT NOT NULL,
                FOREIGN KEY(assigned_client_id) REFERENCES client_manifests(client_id) ON DELETE SET NULL,
                FOREIGN KEY(lease_id) REFERENCES task_leases(task_id) ON DELETE SET NULL
            );

            CREATE INDEX IF NOT EXISTS idx_work_items_run_status
                ON work_items(run_id, status, priority, created_at);
            CREATE INDEX IF NOT EXISTS idx_work_items_status_priority
                ON work_items(status, priority, created_at);
            CREATE INDEX IF NOT EXISTS idx_work_items_assigned_client
                ON work_items(assigned_client_id, status, priority);
            """
        )

    def _migrate_008(self, conn: sqlite3.Connection) -> None:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS plugin_inventory (
                plugin_id TEXT PRIMARY KEY,
                target_client TEXT NOT NULL,
                version TEXT NOT NULL,
                review_state TEXT NOT NULL,
                dashboard_status TEXT NOT NULL,
                uninstall_status TEXT NOT NULL,
                rollback_status TEXT NOT NULL,
                payload_json TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_plugin_inventory_target
                ON plugin_inventory(target_client, review_state, plugin_id);
            CREATE INDEX IF NOT EXISTS idx_plugin_inventory_dashboard
                ON plugin_inventory(dashboard_status, plugin_id);
            """
        )

    def _migrate_009(self, conn: sqlite3.Connection) -> None:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS proof_stream_records (
                record_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                task_id TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                evidence_digest TEXT,
                payload_json TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_proof_stream_run
                ON proof_stream_records(run_id, task_id, created_at, record_id);
            CREATE INDEX IF NOT EXISTS idx_proof_stream_status
                ON proof_stream_records(status, created_at, record_id);

            CREATE TABLE IF NOT EXISTS terminal_stream_records (
                record_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                task_id TEXT NOT NULL,
                kind TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                evidence_digest TEXT,
                payload_json TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_terminal_stream_run
                ON terminal_stream_records(run_id, task_id, created_at, record_id);
            CREATE INDEX IF NOT EXISTS idx_terminal_stream_kind
                ON terminal_stream_records(kind, status, created_at, record_id);
            """
        )

    def _migrate_010(self, conn: sqlite3.Connection) -> None:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS diff_proposals (
                proposal_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                task_id TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                payload_json TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_diff_proposals_run_task
                ON diff_proposals(run_id, task_id, created_at, proposal_id);

            CREATE TABLE IF NOT EXISTS diff_apply_records (
                record_id TEXT PRIMARY KEY,
                proposal_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                FOREIGN KEY(proposal_id) REFERENCES diff_proposals(proposal_id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_diff_apply_records_proposal
                ON diff_apply_records(proposal_id, created_at, record_id);

            CREATE TABLE IF NOT EXISTS run_task_states (
                run_id TEXT NOT NULL,
                task_id TEXT NOT NULL,
                status TEXT NOT NULL,
                work_item_id TEXT,
                updated_at TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                PRIMARY KEY(run_id, task_id)
            );
            CREATE INDEX IF NOT EXISTS idx_run_task_states_status
                ON run_task_states(status, updated_at, run_id, task_id);

            CREATE TABLE IF NOT EXISTS operator_intent_records (
                intent_ref TEXT PRIMARY KEY,
                actor TEXT NOT NULL,
                role TEXT NOT NULL,
                scope TEXT NOT NULL,
                created_at TEXT NOT NULL,
                payload_json TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_operator_intent_scope
                ON operator_intent_records(scope, created_at, intent_ref);
            """
        )

    def _migrate_002(self, conn: sqlite3.Connection) -> None:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS dashboard_activity (
                activity_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                kind TEXT NOT NULL,
                status TEXT NOT NULL,
                client TEXT NOT NULL,
                actor TEXT NOT NULL,
                role TEXT NOT NULL,
                summary TEXT NOT NULL,
                created_at TEXT NOT NULL,
                task_id TEXT,
                evidence_digest TEXT,
                payload_json TEXT NOT NULL,
                FOREIGN KEY(run_id) REFERENCES run_evidence(run_id)
            );
            """
        )

    def _migrate_003(self, conn: sqlite3.Connection) -> None:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS control_plane_events (
                event_id TEXT PRIMARY KEY,
                sequence INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                client_id TEXT NOT NULL,
                run_id TEXT,
                task_id TEXT,
                kind TEXT NOT NULL,
                status TEXT NOT NULL,
                actor TEXT NOT NULL,
                role TEXT NOT NULL,
                evidence_digest TEXT,
                payload_json TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_control_plane_events_order
                ON control_plane_events(sequence, created_at, event_id);
            CREATE INDEX IF NOT EXISTS idx_control_plane_events_run
                ON control_plane_events(run_id, sequence);
            CREATE INDEX IF NOT EXISTS idx_control_plane_events_client
                ON control_plane_events(client_id, sequence);
            """
        )
