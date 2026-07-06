from __future__ import annotations

import json
import mimetypes
import re
import sqlite3
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from pydantic import ValidationError

from vad.adapters.mcp import handle_json_rpc_request
from vad.control_plane.clients import ClientHeartbeat, ClientManifest
from vad.control_plane.leases import TaskLeaseAcquireRequest, TaskLeaseApprovalCheck, TaskLeaseReleaseRequest, TaskLeaseRenewRequest
from vad.control_plane.work_items import WorkItem, WorkItemStatus
from vad.control_plane.plugins import build_governance_dashboard_summary, merge_plugin_status_with_events, seed_plugin_statuses
from vad.evidence.bundle import EvidenceBundle, RunEvidence
from vad.policy.decisions import PolicyDecision
from vad.server.api.approvals import ApprovalRequest, ApprovalService
from vad.server.api.diff_proposals import DiffProposalService
from vad.server.api.clients import ClientRegistryService
from vad.server.api.events import ControlPlaneEventService
from vad.server.api.leases import TaskLeaseService
from vad.server.api.work_items import WorkItemSchedulerService, WorkItemService
from vad.server.db.store import ServerStore


class EvidenceStore:
    def __init__(self, root: Path):
        self.root = root.resolve()

    def load_run(self, run_id: str) -> tuple[RunEvidence, str]:
        path = self._resolve_run_path(run_id)
        payload = json.loads(path.read_text(encoding="utf-8"))
        evidence_data = payload.get("evidence", payload) if isinstance(payload, dict) else payload
        evidence = RunEvidence(**evidence_data)
        return evidence, EvidenceBundle(evidence).compute_hash()

    def _resolve_run_path(self, run_id: str) -> Path:
        if not run_id or "/" in run_id or "\\" in run_id or run_id in {".", ".."}:
            raise FileNotFoundError(run_id)
        path = (self.root / f"{run_id}.json").resolve()
        try:
            path.relative_to(self.root)
        except ValueError as exc:
            raise FileNotFoundError(run_id) from exc
        if not path.is_file():
            raise FileNotFoundError(run_id)
        return path


class VADApi:
    def __init__(self, evidence_root: Path, db_path: Path | None = None):
        self.store = EvidenceStore(evidence_root)
        self.db = ServerStore(db_path) if db_path else None

    def handle(self, method: str, raw_path: str, body: dict[str, Any] | None = None) -> tuple[int, dict[str, Any]]:
        parsed = urlparse(raw_path)
        path = parsed.path.rstrip("/") or "/"
        if method == "GET" and path == "/health":
            return 200, {"status": "ok"}
        if method == "GET" and path == "/ready":
            return 200, {"status": "ready"}
        if method == "GET" and path == "/runs":
            return 200, {"runs": self._list_runs()}
        if method == "GET" and path == "/dashboard":
            return self._dashboard_response()
        if method == "GET" and path == "/dashboard/replay":
            query = parse_qs(parsed.query)
            run_id = query.get("run_id", [None])[0]
            return self._dashboard_replay_response(run_id=run_id)
        if method == "GET" and path == "/plugins/status":
            return 200, self._plugin_status_payload()
        if method == "GET" and path.startswith("/runs/"):
            return self._run_response(path)
        if method == "POST" and path == "/actions/approve":
            return self._approval_response(body or {})
        if method == "POST" and path == "/events":
            return self._event_ingestion_response(body or {})
        if method == "GET" and path == "/events":
            return self._events_response()
        if method == "POST" and path == "/mcp":
            return self._mcp_response(body or {})
        if path == "/leases" and method == "POST":
            return self._lease_acquire_response(body or {})
        if path == "/leases" and method == "GET":
            return self._leases_response()
        if path.startswith("/leases/") and method == "POST":
            return self._lease_action_response(path, body or {})
        if path == "/work-items" and method == "POST":
            return self._work_item_create_response(body or {})
        if path == "/work-items" and method == "GET":
            query = parse_qs(parsed.query)
            return self._work_items_response(
                run_id=query.get("run_id", [None])[0],
                status=query.get("status", [None])[0],
                assigned_client_id=query.get("assigned_client_id", [None])[0],
            )
        if path.startswith("/work-items/") and method == "GET":
            return self._work_item_read_response(path)
        if path.startswith("/work-items/") and method == "POST":
            return self._work_item_action_response(path, body or {})
        if path == "/diff-proposals" and method == "POST":
            return self._diff_proposal_create_response(body or {})
        if path == "/diff-proposals" and method == "GET":
            query = parse_qs(parsed.query)
            return self._diff_proposals_response(
                run_id=query.get("run_id", [None])[0],
                task_id=query.get("task_id", [None])[0],
            )
        if path.startswith("/diff-proposals/") and method == "GET":
            return self._diff_proposal_read_response(path)
        if path.startswith("/diff-proposals/") and path.endswith("/apply") and method == "POST":
            return self._diff_proposal_apply_response(path, body or {})
        if path == "/operator-intents" and method == "POST":
            return self._operator_intent_create_response(body or {})
        if path == "/operator-intents" and method == "GET":
            return self._operator_intents_response()
        if path.startswith("/operator-intents/") and method == "GET":
            return self._operator_intent_read_response(path)
        if path == "/run-task-states" and method == "GET":
            query = parse_qs(parsed.query)
            return self._run_task_states_response(run_id=query.get("run_id", [None])[0])
        if method == "POST" and path == "/clients/register":
            return self._client_register_response(body or {})
        if method == "GET" and path == "/clients":
            return self._clients_response()
        if method == "POST" and path == "/clients/stale-scan":
            return self._clients_stale_scan_response(body or {})
        if method == "POST" and path.startswith("/clients/") and path.endswith("/heartbeat"):
            return self._client_heartbeat_response(path, body or {})
        if method == "DELETE" and path.startswith("/clients/"):
            return self._client_unregister_response(path)
        return 404, {"error": "not_found"}

    def _mcp_response(self, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        response = handle_json_rpc_request(body)
        if response is None:
            return 204, {}
        return 200, response

    def _list_runs(self) -> list[dict[str, str]]:
        if self.db is not None:
            return [
                {
                    "run_id": run.run_id,
                    "final_decision": run.evidence.final_decision,
                    "evidence_digest": run.evidence_digest,
                }
                for run in self.db.list_run_evidence()
            ]
        if not self.store.root.exists():
            return []
        runs = []
        for path in sorted(self.store.root.glob("*.json")):
            try:
                evidence, digest = self.store.load_run(path.stem)
            except (json.JSONDecodeError, ValidationError, FileNotFoundError):
                continue
            runs.append({
                "run_id": evidence.run_id,
                "final_decision": evidence.final_decision,
                "evidence_digest": digest,
            })
        return runs

    def _run_response(self, path: str) -> tuple[int, dict[str, Any]]:
        parts = [unquote(part) for part in path.split("/") if part]
        if len(parts) not in {2, 3}:
            return 404, {"error": "not_found"}
        _, run_id, *tail = parts
        try:
            if self.db is not None:
                stored = self.db.load_run_evidence(run_id)
                evidence = stored.evidence
                digest = stored.evidence_digest
            else:
                evidence, digest = self.store.load_run(run_id)
        except (FileNotFoundError, KeyError):
            return 404, {"error": "run_not_found", "run_id": run_id}
        except (json.JSONDecodeError, ValidationError, ValueError) as exc:
            return 422, {"error": "invalid_evidence", "detail": str(exc)}

        payload = {
            "run_id": evidence.run_id,
            "evidence_digest": digest,
            "evidence": evidence.model_dump(mode="json"),
        }
        if tail == ["evidence"] or not tail:
            return 200, payload
        if tail == ["approvals"] and self.db is not None:
            approvals = self.db.list_approval_events(run_id)
            return 200, {
                "run_id": run_id,
                "approvals": [approval.model_dump(mode="json") for approval in approvals],
            }
        return 404, {"error": "not_found"}

    def _dashboard_response(self) -> tuple[int, dict[str, Any]]:
        if self.db is None:
            decision = PolicyDecision(
                allow=False,
                denials=["dashboard persistence is not configured"],
                requires_human=True,
            )
            return 403, {"decision": decision.model_dump(mode="json")}
        try:
            if self.db.has_control_plane_events():
                payload = self.db.control_plane_dashboard_snapshot()
            else:
                payload = self.db.dashboard_snapshot()
            return 200, self._decorate_dashboard_payload(payload)
        except (json.JSONDecodeError, ValidationError, ValueError) as exc:
            return 422, {"error": "invalid_control_plane_event_ledger", "detail": str(exc)}

    def _dashboard_replay_response(self, *, run_id: str | None = None) -> tuple[int, dict[str, Any]]:
        if self.db is None:
            decision = PolicyDecision(
                allow=False,
                denials=["dashboard persistence is not configured"],
                requires_human=True,
            )
            return 403, {"decision": decision.model_dump(mode="json")}
        try:
            payload = self.db.replay_dashboard_snapshot(run_id=run_id)
            return 200, self._decorate_dashboard_payload(payload)
        except (json.JSONDecodeError, ValidationError, ValueError) as exc:
            return 422, {"error": "invalid_control_plane_event_ledger", "detail": str(exc)}

    def _decorate_dashboard_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        payload["active_clients"] = self._active_clients_payload()
        payload["task_leases"] = self._task_leases_payload()
        task_rows = list(payload.get("task_board") or payload.get("activity", []))
        task_rows.extend(self._work_item_board_rows())
        payload["task_board_columns"] = self._task_board_columns_payload(task_rows)
        structured_proofs = self._structured_proof_status_payload()
        structured_terminal = self._structured_terminal_status_payload()
        payload["proof_status"] = structured_proofs or self._proof_status_payload(payload.get("event_timeline") or payload.get("activity", []))
        payload["terminal_status"] = structured_terminal or self._terminal_status_payload(payload.get("event_timeline") or payload.get("activity", []))
        payload["governance_summary"] = build_governance_dashboard_summary(self.db.list_work_items())
        payload["run_task_states"] = [
            state.model_dump(mode="json") for state in self.db.list_run_task_states()
        ]
        if not payload.get("plugin_status"):
            payload["plugin_status"] = self._plugin_status_payload()["plugins"]
        return payload

    def _active_clients_payload(self) -> list[dict[str, Any]]:
        if self.db is None:
            return []
        now = datetime.now(timezone.utc)
        clients = []
        for snapshot in self.db.list_client_statuses():
            manifest = snapshot.manifest
            heartbeat_at = snapshot.last_heartbeat_at
            heartbeat_age_seconds = None
            if heartbeat_at is not None:
                if heartbeat_at.tzinfo is None:
                    heartbeat_at = heartbeat_at.replace(tzinfo=timezone.utc)
                heartbeat_age_seconds = max(0, int((now - heartbeat_at.astimezone(timezone.utc)).total_seconds()))
            clients.append({
                "client_id": manifest.client_id,
                "display_name": manifest.display_name,
                "client_type": manifest.client_type.value,
                "status": snapshot.status.value,
                "heartbeat_age_seconds": heartbeat_age_seconds,
                "last_heartbeat_at": heartbeat_at.isoformat() if heartbeat_at else None,
                "supported_capabilities": list(manifest.supported_capabilities),
                "connection_mode": manifest.connection_mode.value,
                "trust_state": manifest.trust_state.value,
                "last_run_id": snapshot.last_run_id,
                "last_task_id": snapshot.last_task_id,
                "lost_task_leases": list(snapshot.lost_task_leases),
            })
        return clients

    def _task_leases_payload(self) -> list[dict[str, Any]]:
        if self.db is None:
            return []
        leases = []
        for lease in self.db.list_task_leases():
            leases.append({
                "task_id": lease.task_id,
                "run_id": lease.run_id,
                "client_id": lease.client_id,
                "lease_owner": lease.client_id,
                "actor": lease.actor,
                "role": lease.role,
                "status": lease.status.value,
                "board_status": self._lease_board_status(lease.status.value),
                "acquired_at": lease.acquired_at.isoformat(),
                "expires_at": lease.expires_at.isoformat(),
                "release_reason": lease.release_reason,
            })
        return leases

    def _work_item_board_rows(self) -> list[dict[str, Any]]:
        if self.db is None:
            return []
        rows = []
        for item in self.db.list_work_items():
            rows.append({
                "task_id": item.work_item_id,
                "work_item_id": item.work_item_id,
                "run_id": item.run_id,
                "client": item.assigned_client_id,
                "client_id": item.assigned_client_id,
                "actor": item.assigned_client_id,
                "role": item.role,
                "status": self._work_item_board_status(item.status),
                "work_item_status": item.status.value,
                "kind": "work_item",
                "summary": item.title,
                "description": item.description,
                "priority": item.priority,
                "requested_capability": item.requested_capability,
                "governance": item.governance.model_dump(mode="json") if item.governance else None,
                "updated_at": item.updated_at.isoformat(),
                "event_id": None,
                "lease_id": item.lease_id,
                "evidence_digest": item.evidence_digest,
                "blocked_reason": item.blocked_reason,
            })
        return rows

    def _task_board_columns_payload(self, task_rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
        columns: dict[str, list[dict[str, Any]]] = {
            "active": [],
            "blocked": [],
            "passed": [],
            "failed": [],
            "needs_human": [],
        }
        leases_by_task = {lease["task_id"]: lease for lease in self._task_leases_payload()}
        board_rows: dict[str, dict[str, Any]] = {}
        for row in task_rows:
            task_id = row.get("task_id") or row.get("activity_id")
            if not task_id:
                continue
            board_rows[task_id] = {
                "task_id": task_id,
                "run_id": row.get("run_id"),
                "client": row.get("client") or row.get("client_id"),
                "client_id": row.get("client_id"),
                "actor": row.get("actor"),
                "role": row.get("role"),
                "status": row.get("status", "active"),
                "work_item_id": row.get("work_item_id"),
                "work_item_status": row.get("work_item_status"),
                "kind": row.get("kind"),
                "summary": row.get("summary", ""),
                "description": row.get("description"),
                "priority": row.get("priority"),
                "requested_capability": row.get("requested_capability"),
                "governance": row.get("governance"),
                "updated_at": row.get("updated_at") or row.get("created_at"),
                "event_id": row.get("event_id") or row.get("activity_id"),
                "lease_id": row.get("lease_id"),
                "evidence_digest": row.get("evidence_digest"),
                "blocked_reason": row.get("blocked_reason"),
            }
        for task_id, lease in leases_by_task.items():
            board_rows.setdefault(task_id, {
                "task_id": task_id,
                "run_id": lease["run_id"],
                "client": lease["client_id"],
                "client_id": lease["client_id"],
                "actor": lease["actor"],
                "role": lease["role"],
                "status": lease["board_status"],
                "kind": "task_lease",
                "summary": f"Task lease held by {lease['lease_owner']}.",
                "updated_at": lease["acquired_at"],
                "event_id": None,
            })
            board_rows[task_id].update({
                "lease_owner": lease["lease_owner"],
                "lease_status": lease["status"],
                "lease_expires_at": lease["expires_at"],
                "lease_actor": lease["actor"],
                "lease_role": lease["role"],
                "release_reason": lease["release_reason"],
            })
            board_rows[task_id]["status"] = self._board_status(board_rows[task_id].get("status"), lease["board_status"])
        for row in board_rows.values():
            lease_id = row.get("lease_id")
            if not lease_id or lease_id not in leases_by_task:
                continue
            lease = leases_by_task[lease_id]
            row.update({
                "lease_owner": lease["lease_owner"],
                "lease_status": lease["status"],
                "lease_expires_at": lease["expires_at"],
                "lease_actor": lease["actor"],
                "lease_role": lease["role"],
                "release_reason": lease["release_reason"],
            })
            row["status"] = self._board_status(row.get("status"), lease["board_status"])
        for row in board_rows.values():
            columns[self._board_status(row.get("status"))].append(row)
        return {status: sorted(rows, key=lambda item: (str(item.get("run_id") or ""), str(item.get("task_id") or ""))) for status, rows in columns.items()}

    def _board_status(self, status: Any, fallback: str = "active") -> str:
        normalized = str(status or fallback)
        if normalized in {"active", "blocked", "passed", "failed", "needs_human"}:
            return normalized
        if normalized in {"stale", "expired"}:
            return "failed"
        if normalized == "released":
            return "passed"
        return fallback if fallback in {"active", "blocked", "passed", "failed", "needs_human"} else "active"

    def _lease_board_status(self, status: str) -> str:
        if status == "released":
            return "passed"
        if status == "expired":
            return "failed"
        if status == "blocked":
            return "blocked"
        return "active"

    def _work_item_board_status(self, status: WorkItemStatus) -> str:
        if status in {
            WorkItemStatus.PLANNED,
            WorkItemStatus.QUEUED,
            WorkItemStatus.ASSIGNED,
            WorkItemStatus.RUNNING,
            WorkItemStatus.REQUEUED,
            WorkItemStatus.VERIFYING,
        }:
            return "active"
        if status == WorkItemStatus.BLOCKED:
            return "blocked"
        if status == WorkItemStatus.WAITING_FOR_HUMAN:
            return "needs_human"
        if status in {WorkItemStatus.APPROVED, WorkItemStatus.COMPLETED}:
            return "passed"
        if status in {WorkItemStatus.FAILED, WorkItemStatus.CANCELLED}:
            return "failed"
        return "active"

    def _proof_status_payload(self, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        proofs: dict[str, dict[str, Any]] = {}
        recovery_by_task: dict[tuple[str | None, str | None], dict[str, Any]] = {}
        for event in events:
            key = (event.get("run_id"), event.get("task_id"))
            if event.get("kind") == "recovery_action":
                recovery_by_task[key] = event
        for event in events:
            if event.get("kind") not in {"proof_started", "proof_finished"}:
                continue
            task_id = event.get("task_id") or event.get("event_id")
            proof = proofs.setdefault(task_id, {
                "task_id": task_id,
                "run_id": event.get("run_id"),
                "client": event.get("client") or event.get("client_id"),
                "client_id": event.get("client_id"),
                "actor": event.get("actor"),
                "role": event.get("role"),
                "status": "active",
                "started_at": None,
                "finished_at": None,
                "summary": "",
                "evidence_digest": event.get("evidence_digest"),
                "recovery_event_id": None,
                "recovery_evidence_url": None,
            })
            proof.update({
                "run_id": event.get("run_id"),
                "client": event.get("client") or event.get("client_id"),
                "client_id": event.get("client_id"),
                "actor": event.get("actor"),
                "role": event.get("role"),
                "summary": self._redact_log_text(event.get("summary", "")),
                "evidence_digest": event.get("evidence_digest") or proof.get("evidence_digest"),
            })
            if event.get("kind") == "proof_started":
                proof["started_at"] = event.get("created_at") or event.get("updated_at")
                proof["status"] = "active"
            if event.get("kind") == "proof_finished":
                proof["finished_at"] = event.get("created_at") or event.get("updated_at")
                proof["status"] = event.get("status", "failed")
                if event.get("status") == "failed":
                    recovery = recovery_by_task.get((event.get("run_id"), event.get("task_id")))
                    proof["recovery_event_id"] = recovery.get("event_id") if recovery else None
                    proof["recovery_evidence_url"] = f"/runs/{event.get('run_id')}/evidence" if event.get("run_id") else None
        return [proofs[key] for key in sorted(proofs)]

    def _structured_proof_status_payload(self) -> list[dict[str, Any]]:
        if self.db is None:
            return []
        rows = []
        for record in self.db.list_proof_stream_records():
            row = record.model_dump(mode="json")
            row["summary"] = self._redact_log_text(row.get("summary", ""))
            rows.append(row)
        return rows

    def _terminal_status_payload(self, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        rows = []
        for event in events:
            if event.get("kind") not in {"proof_started", "proof_finished", "tool_call_started", "tool_call_finished", "policy_denied", "recovery_action"}:
                continue
            rows.append({
                "event_id": event.get("event_id") or event.get("activity_id"),
                "run_id": event.get("run_id"),
                "task_id": event.get("task_id"),
                "kind": event.get("kind"),
                "status": event.get("status"),
                "client": event.get("client") or event.get("client_id"),
                "role": event.get("role"),
                "created_at": event.get("created_at") or event.get("updated_at"),
                "summary": self._redact_log_text(event.get("summary", "")),
            })
        return rows

    def _structured_terminal_status_payload(self) -> list[dict[str, Any]]:
        if self.db is None:
            return []
        rows = []
        for record in self.db.list_terminal_stream_records():
            row = record.model_dump(mode="json")
            row["summary"] = self._redact_log_text(row.get("summary", ""))
            rows.append(row)
        return rows

    def _redact_log_text(self, value: Any) -> str:
        text = str(value or "")
        patterns = [
            r"(?i)(api[_-]?key|token|password|secret|private[_-]?key)\s*[:=]\s*[^,\s;]+",
            r"sk-[A-Za-z0-9_-]+",
        ]
        redacted = text
        for pattern in patterns:
            redacted = re.sub(pattern, lambda match: f"{match.group(1) if match.lastindex else 'secret'}=[REDACTED]", redacted)
        return redacted

    def _plugin_status_payload(self) -> dict[str, Any]:
        inventory = self.db.list_plugin_inventory_records() if self.db is not None else []
        if inventory:
            events = self.db.list_control_plane_events() if self.db is not None else []
            if events:
                plugins = merge_plugin_status_with_events(inventory, events)
                source = "inventory_events"
            else:
                plugins = [record.to_status_record().model_dump(mode="json") for record in inventory]
                source = "inventory"
        else:
            plugins = [plugin.model_dump(mode="json") for plugin in seed_plugin_statuses()]
            source = "seeded"
        counts: dict[str, int] = {}
        for plugin in plugins:
            counts[plugin["status"]] = counts.get(plugin["status"], 0) + 1
        return {
            "status": source,
            "plugins": plugins,
            "status_counts": dict(sorted(counts.items())),
        }

    def _approval_response(self, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        if self.db is None:
            decision = PolicyDecision(
                allow=False,
                denials=["approval storage is not configured"],
                requires_human=True,
            )
            return 403, {"decision": decision.model_dump(mode="json")}
        try:
            request = ApprovalRequest(**body)
            result = ApprovalService(self.db).record_approval(request)
        except KeyError:
            return 404, {"error": "run_not_found", "run_id": body.get("run_id")}
        except ValidationError as exc:
            return 400, {"error": "invalid_approval_request", "detail": str(exc)}
        return result.status_code, {"approval": result.event.model_dump(mode="json")}

    def _event_ingestion_response(self, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        if self.db is None:
            decision = PolicyDecision(
                allow=False,
                denials=["event storage is not configured"],
                requires_human=True,
            )
            return 403, {"decision": decision.model_dump(mode="json")}
        try:
            result = ControlPlaneEventService(self.db).ingest(body)
        except ValidationError as exc:
            return 400, {"error": "invalid_control_plane_event", "detail": str(exc)}
        except sqlite3.IntegrityError:
            return 409, {"error": "duplicate_control_plane_event", "event_id": body.get("event_id")}
        return result.status_code, {
            "event": result.event.model_dump(mode="json"),
            "decision": result.decision.model_dump(mode="json"),
        }

    def _events_response(self) -> tuple[int, dict[str, Any]]:
        if self.db is None:
            decision = PolicyDecision(
                allow=False,
                denials=["event storage is not configured"],
                requires_human=True,
            )
            return 403, {"decision": decision.model_dump(mode="json")}
        events = self.db.list_control_plane_events()
        return 200, {"events": [event.model_dump(mode="json") for event in events]}

    def _lease_acquire_response(self, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        if self.db is None:
            decision = PolicyDecision(
                allow=False,
                denials=["task lease storage is not configured"],
                requires_human=True,
            )
            return 403, {"decision": decision.model_dump(mode="json")}
        try:
            result = TaskLeaseService(self.db).acquire(TaskLeaseAcquireRequest(**body))
        except KeyError:
            return 404, {"error": "client_not_found", "client_id": body.get("client_id")}
        except ValidationError as exc:
            return 400, {"error": "invalid_task_lease", "detail": str(exc)}
        return result.status_code, self._lease_result_payload(result)

    def _leases_response(self) -> tuple[int, dict[str, Any]]:
        if self.db is None:
            decision = PolicyDecision(
                allow=False,
                denials=["task lease storage is not configured"],
                requires_human=True,
            )
            return 403, {"decision": decision.model_dump(mode="json")}
        leases = self.db.list_task_leases()
        return 200, {"leases": [lease.model_dump(mode="json") for lease in leases]}

    def _lease_action_response(self, path: str, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        if self.db is None:
            decision = PolicyDecision(
                allow=False,
                denials=["task lease storage is not configured"],
                requires_human=True,
            )
            return 403, {"decision": decision.model_dump(mode="json")}
        parts = [unquote(part) for part in path.split("/") if part]
        if len(parts) != 3:
            return 404, {"error": "not_found"}
        _, task_id, action = parts
        service = TaskLeaseService(self.db)
        try:
            if action == "renew":
                result = service.renew(task_id, TaskLeaseRenewRequest(**body))
                return result.status_code, self._lease_result_payload(result)
            if action == "release":
                result = service.release(task_id, TaskLeaseReleaseRequest(**body))
                return result.status_code, self._lease_result_payload(result)
            if action == "expire":
                result = service.expire(task_id)
                return result.status_code, self._lease_result_payload(result)
            if action == "approval-check":
                decision = service.check_approval_transition(task_id, TaskLeaseApprovalCheck(**body))
                return 200 if decision.allow else 403, {"decision": decision.model_dump(mode="json")}
        except KeyError:
            return 404, {"error": "task_lease_not_found", "task_id": task_id}
        except PermissionError as exc:
            decision = PolicyDecision(allow=False, denials=[str(exc)], requires_human=True)
            return 403, {"decision": decision.model_dump(mode="json")}
        except (ValidationError, ValueError) as exc:
            return 400, {"error": "invalid_task_lease", "detail": str(exc)}
        return 404, {"error": "not_found"}

    def _lease_result_payload(self, result) -> dict[str, Any]:
        return {
            "lease": result.lease.model_dump(mode="json"),
            "event": result.event.model_dump(mode="json"),
            "decision": result.decision.model_dump(mode="json"),
        }

    def _work_item_create_response(self, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        if self.db is None:
            decision = PolicyDecision(
                allow=False,
                denials=["work item storage is not configured"],
                requires_human=True,
            )
            return 403, {"decision": decision.model_dump(mode="json")}
        try:
            item = WorkItem(**body["work_item"] if "work_item" in body else body)
            result = WorkItemService(self.db).create(
                item,
                actor=body.get("actor", "operator"),
                role=body.get("role", "operator"),
                client_id=body.get("client_id", "control-plane"),
                summary=body.get("summary"),
            )
        except ValidationError as exc:
            return 400, {"error": "invalid_work_item", "detail": str(exc)}
        except sqlite3.IntegrityError:
            return 409, {"error": "duplicate_work_item", "work_item_id": body.get("work_item_id")}
        return result.status_code, self._work_item_result_payload(result)

    def _work_items_response(
        self,
        *,
        run_id: str | None = None,
        status: str | None = None,
        assigned_client_id: str | None = None,
    ) -> tuple[int, dict[str, Any]]:
        if self.db is None:
            decision = PolicyDecision(
                allow=False,
                denials=["work item storage is not configured"],
                requires_human=True,
            )
            return 403, {"decision": decision.model_dump(mode="json")}
        try:
            status_filter = WorkItemStatus(status) if status else None
        except ValueError as exc:
            return 400, {"error": "invalid_work_item_status", "detail": str(exc)}
        items = self.db.list_work_items(
            run_id=run_id,
            status=status_filter,
            assigned_client_id=assigned_client_id,
        )
        return 200, {"work_items": [item.model_dump(mode="json") for item in items]}

    def _work_item_read_response(self, path: str) -> tuple[int, dict[str, Any]]:
        if self.db is None:
            decision = PolicyDecision(
                allow=False,
                denials=["work item storage is not configured"],
                requires_human=True,
            )
            return 403, {"decision": decision.model_dump(mode="json")}
        parts = [unquote(part) for part in path.split("/") if part]
        if len(parts) != 2:
            return 404, {"error": "not_found"}
        try:
            item = self.db.load_work_item(parts[1])
        except KeyError:
            return 404, {"error": "work_item_not_found", "work_item_id": parts[1]}
        return 200, {"work_item": item.model_dump(mode="json")}

    def _work_item_action_response(self, path: str, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        if self.db is None:
            decision = PolicyDecision(
                allow=False,
                denials=["work item storage is not configured"],
                requires_human=True,
            )
            return 403, {"decision": decision.model_dump(mode="json")}
        parts = [unquote(part) for part in path.split("/") if part]
        if len(parts) != 3:
            return 404, {"error": "not_found"}
        _, work_item_id, action = parts
        service = WorkItemService(self.db)
        actor = body.get("actor", "operator")
        role = body.get("role", "operator")
        client_id = body.get("client_id", "control-plane")
        try:
            if action == "assign":
                assigned_client_id = body.get("assigned_client_id")
                if assigned_client_id:
                    result = service.assign(
                        work_item_id,
                        actor=actor,
                        role=role,
                        client_id=client_id,
                        assigned_client_id=assigned_client_id,
                        lease_id=body.get("lease_id"),
                    )
                    return result.status_code, self._work_item_result_payload(result)
                scheduled = WorkItemSchedulerService(self.db).schedule_work_item(
                    work_item_id,
                    actor=actor,
                    role=role,
                    client_id=client_id,
                )
                return scheduled.status_code, self._scheduler_result_payload(scheduled)
            if action == "block":
                result = service.block(
                    work_item_id,
                    actor=actor,
                    role=role,
                    client_id=client_id,
                    reason=body.get("reason", "blocked"),
                )
                return result.status_code, self._work_item_result_payload(result)
            if action == "complete":
                result = service.complete(
                    work_item_id,
                    actor=actor,
                    role=role,
                    client_id=client_id,
                    evidence_digest=body.get("evidence_digest"),
                )
                return result.status_code, self._work_item_result_payload(result)
            if action == "fail":
                result = service.fail(work_item_id, actor=actor, role=role, client_id=client_id)
                return result.status_code, self._work_item_result_payload(result)
            if action == "cancel":
                result = service.cancel(work_item_id, actor=actor, role=role, client_id=client_id)
                return result.status_code, self._work_item_result_payload(result)
            if action == "requeue":
                result = service.requeue(work_item_id, actor=actor, role=role, client_id=client_id)
                return result.status_code, self._work_item_result_payload(result)
        except KeyError:
            return 404, {"error": "work_item_not_found", "work_item_id": work_item_id}
        except ValidationError as exc:
            return 400, {"error": "invalid_work_item", "detail": str(exc)}
        return 404, {"error": "not_found"}

    def _work_item_result_payload(self, result) -> dict[str, Any]:
        return {
            "work_item": result.work_item.model_dump(mode="json"),
            "event": result.event.model_dump(mode="json"),
            "decision": result.decision.model_dump(mode="json"),
        }

    def _scheduler_result_payload(self, result) -> dict[str, Any]:
        return {
            "work_item": result.work_item.model_dump(mode="json") if result.work_item else None,
            "selected_client_id": result.selected_client_id,
            "event": result.event.model_dump(mode="json") if result.event else None,
            "decision": result.decision.model_dump(mode="json"),
        }

    def _client_register_response(self, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        if self.db is None:
            decision = PolicyDecision(
                allow=False,
                denials=["client registry storage is not configured"],
                requires_human=True,
            )
            return 403, {"decision": decision.model_dump(mode="json")}
        try:
            result = ClientRegistryService(self.db).register(ClientManifest(**body))
        except ValidationError as exc:
            return 400, {"error": "invalid_client_manifest", "detail": str(exc)}
        except sqlite3.IntegrityError:
            return 409, {"error": "duplicate_client", "client_id": body.get("client_id")}
        return 201, {
            "client": result.manifest.model_dump(mode="json"),
            "event": result.event.model_dump(mode="json"),
        }

    def _clients_response(self) -> tuple[int, dict[str, Any]]:
        if self.db is None:
            decision = PolicyDecision(
                allow=False,
                denials=["client registry storage is not configured"],
                requires_human=True,
            )
            return 403, {"decision": decision.model_dump(mode="json")}
        statuses = ClientRegistryService(self.db).list_statuses()
        return 200, {"clients": [status.model_dump(mode="json") for status in statuses]}

    def _client_heartbeat_response(self, path: str, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        if self.db is None:
            decision = PolicyDecision(
                allow=False,
                denials=["client registry storage is not configured"],
                requires_human=True,
            )
            return 403, {"decision": decision.model_dump(mode="json")}
        parts = [unquote(part) for part in path.split("/") if part]
        if len(parts) != 3 or parts[2] != "heartbeat":
            return 404, {"error": "not_found"}
        body = {**body, "client_id": parts[1]}
        try:
            result = ClientRegistryService(self.db).heartbeat(ClientHeartbeat(**body))
        except KeyError:
            return 404, {"error": "client_not_found", "client_id": parts[1]}
        except ValidationError as exc:
            return 400, {"error": "invalid_client_heartbeat", "detail": str(exc)}
        return 201, {
            "heartbeat": result.heartbeat.model_dump(mode="json"),
            "client": result.snapshot.model_dump(mode="json"),
            "event": result.event.model_dump(mode="json"),
        }

    def _clients_stale_scan_response(self, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        if self.db is None:
            decision = PolicyDecision(
                allow=False,
                denials=["client registry storage is not configured"],
                requires_human=True,
            )
            return 403, {"decision": decision.model_dump(mode="json")}
        stale_after_seconds = int(body.get("stale_after_seconds", 120))
        auto_reassign = bool(body.get("auto_reassign", False))
        stale = ClientRegistryService(self.db).mark_stale_clients_with_recovery(
            stale_after_seconds=stale_after_seconds,
            auto_reassign=auto_reassign,
        )
        return 200, {
            "stale_clients": [result.snapshot.model_dump(mode="json") for result in stale],
            "recovered_work_items": [
                {
                    "work_item": transition.work_item.model_dump(mode="json"),
                    "event": transition.event.model_dump(mode="json"),
                    "decision": transition.decision.model_dump(mode="json"),
                }
                for result in stale
                for transition in result.recovered_work_items
            ],
            "reassigned_work_items": [
                {
                    "work_item": decision.work_item.model_dump(mode="json") if decision.work_item else None,
                    "selected_client_id": decision.selected_client_id,
                    "event": decision.event.model_dump(mode="json") if decision.event else None,
                    "decision": decision.decision.model_dump(mode="json"),
                }
                for result in stale
                for decision in result.reassigned_work_items
            ],
            "recovery_events": [
                event.model_dump(mode="json")
                for result in stale
                for event in result.recovery_events
            ],
        }

    def _diff_proposal_create_response(self, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        if self.db is None:
            decision = PolicyDecision(
                allow=False,
                denials=["diff proposal storage is not configured"],
                requires_human=True,
            )
            return 403, {"decision": decision.model_dump(mode="json")}
        result = DiffProposalService(self.db).create(body)
        return result.status_code, {
            "proposal": result.proposal.model_dump(mode="json") if result.proposal else None,
            "decision": result.decision.model_dump(mode="json"),
        }

    def _diff_proposals_response(
        self,
        *,
        run_id: str | None = None,
        task_id: str | None = None,
    ) -> tuple[int, dict[str, Any]]:
        if self.db is None:
            decision = PolicyDecision(
                allow=False,
                denials=["diff proposal storage is not configured"],
                requires_human=True,
            )
            return 403, {"decision": decision.model_dump(mode="json")}
        proposals = DiffProposalService(self.db).list(run_id=run_id, task_id=task_id)
        return 200, {"proposals": [proposal.model_dump(mode="json") for proposal in proposals]}

    def _diff_proposal_read_response(self, path: str) -> tuple[int, dict[str, Any]]:
        if self.db is None:
            decision = PolicyDecision(
                allow=False,
                denials=["diff proposal storage is not configured"],
                requires_human=True,
            )
            return 403, {"decision": decision.model_dump(mode="json")}
        parts = [unquote(part) for part in path.split("/") if part]
        if len(parts) != 2:
            return 404, {"error": "not_found"}
        try:
            proposal = DiffProposalService(self.db).read(parts[1])
        except KeyError:
            return 404, {"error": "diff_proposal_not_found", "proposal_id": parts[1]}
        apply_records = self.db.list_diff_apply_records(proposal_id=parts[1])
        return 200, {
            "proposal": proposal.model_dump(mode="json"),
            "apply_records": [record.model_dump(mode="json") for record in apply_records],
        }

    def _diff_proposal_apply_response(self, path: str, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        if self.db is None:
            decision = PolicyDecision(
                allow=False,
                denials=["diff proposal storage is not configured"],
                requires_human=True,
            )
            return 403, {"decision": decision.model_dump(mode="json")}
        parts = [unquote(part) for part in path.split("/") if part]
        if len(parts) != 3 or parts[2] != "apply":
            return 404, {"error": "not_found"}
        workspace_root = body.get("workspace_root", str(self.store.root))
        verifier = PolicyDecision(**body.get("verifier_decision", {"allow": False, "denials": ["missing verifier decision"]}))
        guardian = PolicyDecision(**body.get("release_guardian_decision", {"allow": False, "denials": ["missing release guardian decision"]}))
        result = DiffProposalService(self.db).apply(
            parts[1],
            workspace_root=workspace_root,
            verifier_decision=verifier,
            release_guardian_decision=guardian,
        )
        return result.status_code, {
            "proposal": result.proposal.model_dump(mode="json") if result.proposal else None,
            "apply_record": result.apply_record.model_dump(mode="json") if result.apply_record else None,
            "decision": result.decision.model_dump(mode="json"),
        }

    def _operator_intent_create_response(self, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        if self.db is None:
            decision = PolicyDecision(
                allow=False,
                denials=["operator intent storage is not configured"],
                requires_human=True,
            )
            return 403, {"decision": decision.model_dump(mode="json")}
        from vad.control_plane.governance_records import OperatorIntentRecord

        try:
            record = OperatorIntentRecord(**body)
        except ValidationError as exc:
            return 400, {"error": "invalid_operator_intent", "detail": str(exc)}
        saved = self.db.save_operator_intent_record(record)
        return 201, {"operator_intent": saved.model_dump(mode="json")}

    def _operator_intents_response(self) -> tuple[int, dict[str, Any]]:
        if self.db is None:
            decision = PolicyDecision(
                allow=False,
                denials=["operator intent storage is not configured"],
                requires_human=True,
            )
            return 403, {"decision": decision.model_dump(mode="json")}
        records = self.db.list_operator_intent_records()
        return 200, {"operator_intents": [record.model_dump(mode="json") for record in records]}

    def _operator_intent_read_response(self, path: str) -> tuple[int, dict[str, Any]]:
        if self.db is None:
            decision = PolicyDecision(
                allow=False,
                denials=["operator intent storage is not configured"],
                requires_human=True,
            )
            return 403, {"decision": decision.model_dump(mode="json")}
        parts = [unquote(part) for part in path.split("/") if part]
        if len(parts) != 2:
            return 404, {"error": "not_found"}
        try:
            record = self.db.load_operator_intent_record(parts[1])
        except KeyError:
            return 404, {"error": "operator_intent_not_found", "intent_ref": parts[1]}
        return 200, {"operator_intent": record.model_dump(mode="json")}

    def _run_task_states_response(self, *, run_id: str | None = None) -> tuple[int, dict[str, Any]]:
        if self.db is None:
            decision = PolicyDecision(
                allow=False,
                denials=["run task state storage is not configured"],
                requires_human=True,
            )
            return 403, {"decision": decision.model_dump(mode="json")}
        states = self.db.list_run_task_states(run_id=run_id)
        return 200, {"run_task_states": [state.model_dump(mode="json") for state in states]}

    def _client_unregister_response(self, path: str) -> tuple[int, dict[str, Any]]:
        if self.db is None:
            decision = PolicyDecision(
                allow=False,
                denials=["client registry storage is not configured"],
                requires_human=True,
            )
            return 403, {"decision": decision.model_dump(mode="json")}
        parts = [unquote(part) for part in path.split("/") if part]
        if len(parts) != 2:
            return 404, {"error": "not_found"}
        try:
            result = ClientRegistryService(self.db).unregister(parts[1])
        except KeyError:
            return 404, {"error": "client_not_found", "client_id": parts[1]}
        return 200, {
            "client": result.manifest.model_dump(mode="json"),
            "event": result.event.model_dump(mode="json"),
        }


def make_handler(api: VADApi, ui_root: Path | None = None):
    resolved_ui_root = ui_root.resolve() if ui_root else None

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if resolved_ui_root is not None and self._send_static_if_available():
                return
            self._send(*api.handle("GET", self.path))

        def do_POST(self):
            if self.path.startswith(("/events", "/mcp", "/work-items", "/diff-proposals", "/operator-intents")) and not self._is_local_request():
                self._send(403, {"error": "local_only_route"})
                return
            try:
                body = self._read_json_body()
            except json.JSONDecodeError as exc:
                self._send(400, {"error": "invalid_json", "detail": str(exc)})
                return
            self._send(*api.handle("POST", self.path, body=body))

        def do_DELETE(self):
            self._send(*api.handle("DELETE", self.path))

        def log_message(self, format, *args):
            return None

        def _send(self, status: int, payload: dict[str, Any]):
            body = json.dumps(payload, indent=2).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_static_if_available(self) -> bool:
            parsed = urlparse(self.path)
            if parsed.path.startswith(("/runs", "/health", "/ready", "/actions", "/dashboard", "/plugins", "/events", "/clients", "/leases", "/work-items", "/diff-proposals", "/operator-intents", "/run-task-states", "/mcp")):
                return False
            relative = "index.html" if parsed.path in {"", "/"} else unquote(parsed.path.lstrip("/"))
            candidate = (resolved_ui_root / relative).resolve()
            try:
                candidate.relative_to(resolved_ui_root)
            except ValueError:
                self._send(404, {"error": "not_found"})
                return True
            if not candidate.is_file():
                self._send(404, {"error": "not_found"})
                return True
            body = candidate.read_bytes()
            content_type = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
            if content_type.startswith("text/") or candidate.suffix in {".js", ".css"}:
                content_type = f"{content_type}; charset=utf-8"
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return True

        def _read_json_body(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length", "0"))
            if length == 0:
                return {}
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            return payload if isinstance(payload, dict) else {"payload": payload}

        def _is_local_request(self) -> bool:
            host = self.client_address[0]
            return host in {"127.0.0.1", "::1", "localhost"}

    return Handler


def create_server(
    host: str,
    port: int,
    evidence_root: Path,
    db_path: Path | None = None,
    ui_root: Path | None = None,
) -> ThreadingHTTPServer:
    api = VADApi(evidence_root, db_path=db_path)
    return ThreadingHTTPServer((host, port), make_handler(api, ui_root=ui_root))
