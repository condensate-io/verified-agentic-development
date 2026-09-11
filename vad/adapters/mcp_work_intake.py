from __future__ import annotations

import json
import os
import uuid
from typing import Any, Dict, Optional

from vad.connectors.models import AttestationSyncPayload, ExternalWorkRef
from vad.connectors.registry import get_connector_registry
from vad.control_plane.leases import TaskLease, TaskLeaseAcquireRequest, TaskLeaseStatus
from vad.control_plane.work_items import WorkItemStatus
from vad.server.api.leases import TaskLeaseService
from vad.server.api.work_items import WorkItemService
from vad.server.db.store import ServerStore


def _get_store(params: Dict[str, Any]) -> Optional[ServerStore]:
    """Resolves ServerStore from params (for testing/injection) or from environment."""
    from pathlib import Path
    if "db_path" in params:
        p = params["db_path"]
        return ServerStore(Path(p) if isinstance(p, str) else p)
    env_db = os.environ.get("VAD_SERVER_DB")
    if env_db:
        return ServerStore(Path(env_db))
    return None


def handle_work_intake_discover(params: Dict[str, Any]) -> Dict[str, Any]:
    store = _get_store(params)
    if store is None:
        return {
            "content": [{"type": "text", "text": "Storage unavailable: VAD_SERVER_DB not configured"}],
            "isError": True,
        }

    role = params.get("role", "developer")
    limit = int(params.get("limit", 10))
    capability = params.get("capability")

    queued = store.list_work_items(status=WorkItemStatus.QUEUED)
    matched = []
    for wi in queued:
        if wi.role == role:
            if capability is None or wi.requested_capability == capability:
                matched.append({
                    "work_item_id": wi.work_item_id,
                    "title": wi.title,
                    "description": wi.description,
                    "priority": wi.priority,
                    "role": wi.role,
                    "requested_capability": wi.requested_capability,
                    "governance": wi.governance.model_dump(mode="json") if wi.governance else None,
                })
        if len(matched) >= limit:
            break

    return {
        "content": [{"type": "text", "text": json.dumps({"work_items": matched, "count": len(matched)}, indent=2)}],
        "isError": False,
    }


def handle_work_intake_claim(params: Dict[str, Any]) -> Dict[str, Any]:
    store = _get_store(params)
    if store is None:
        return {
            "content": [{"type": "text", "text": "Storage unavailable"}],
            "isError": True,
        }

    work_item_id = params.get("work_item_id")
    client_id = params.get("client_id")
    duration = int(params.get("lease_duration_seconds", 600))

    if not work_item_id or not client_id:
        return {
            "content": [{"type": "text", "text": "work_item_id and client_id are required"}],
            "isError": True,
        }

    try:
        current_item = store.load_work_item(work_item_id)
    except KeyError:
        return {
            "content": [{"type": "text", "text": f"Work item '{work_item_id}' not found"}],
            "isError": True,
        }

    if current_item.status not in (WorkItemStatus.QUEUED, WorkItemStatus.REQUEUED):
        return {
            "content": [{"type": "text", "text": f"Work item cannot be claimed in status '{current_item.status.value}'"}],
            "isError": True,
        }

    lease_service = TaskLeaseService(store)
    # Provision lease
    lease_req = TaskLeaseAcquireRequest(
        task_id=work_item_id,
        run_id=current_item.run_id,
        client_id=client_id,
        role=current_item.role,
        actor=client_id,
        ttl_seconds=duration,
    )
    lease_res = lease_service.acquire(lease_req)
    if lease_res.status_code not in (200, 201):
        return {
            "content": [{"type": "text", "text": f"Lease acquisition denied: {lease_res.decision.denials}"}],
            "isError": True,
        }

    # In ServerStore, work_items.lease_id references task_leases(task_id)
    actual_lease_id = work_item_id

    # Transition work item to assigned then running
    service = WorkItemService(store)
    t1 = service.transition(
        work_item_id,
        status=WorkItemStatus.ASSIGNED,
        actor=client_id,
        role=current_item.role,
        client_id=client_id,
        assigned_client_id=client_id,
        lease_id=actual_lease_id,
    )
    t2 = service.transition(
        work_item_id,
        status=WorkItemStatus.RUNNING,
        actor=client_id,
        role=current_item.role,
        client_id=client_id,
        assigned_client_id=client_id,
        lease_id=actual_lease_id,
    )

    return {
        "content": [{
            "type": "text",
            "text": json.dumps({
                "status": "claimed",
                "work_item_id": work_item_id,
                "lease_id": actual_lease_id,
                "expires_at": lease_res.lease.expires_at.isoformat(),
                "work_item": t2.work_item.model_dump(mode="json"),
            }, indent=2)
        }],
        "isError": False,
    }


def handle_work_intake_report_progress(params: Dict[str, Any]) -> Dict[str, Any]:
    store = _get_store(params)
    if store is None:
        return {"content": [{"type": "text", "text": "Storage unavailable"}], "isError": True}

    work_item_id = params.get("work_item_id")
    lease_id = params.get("lease_id")
    message = params.get("message")
    percent = params.get("percent_complete")

    if not work_item_id or not lease_id or not message:
        return {"content": [{"type": "text", "text": "work_item_id, lease_id, and message are required"}], "isError": True}

    try:
        current_item = store.load_work_item(work_item_id)
    except KeyError:
        return {"content": [{"type": "text", "text": f"Work item '{work_item_id}' not found"}], "isError": True}

    if current_item.lease_id != lease_id:
        return {"content": [{"type": "text", "text": f"Invalid lease_id for work item"}], "isError": True}

    # Attempt outbound comment sync if connector registered
    _sync_comment_to_connector(current_item.work_item_id, f"Progress update: {message}")

    return {
        "content": [{
            "type": "text",
            "text": json.dumps({
                "status": "progress_reported",
                "work_item_id": work_item_id,
                "message": message,
                "percent_complete": percent,
            }, indent=2)
        }],
        "isError": False,
    }


def handle_work_intake_submit_proof(params: Dict[str, Any]) -> Dict[str, Any]:
    store = _get_store(params)
    if store is None:
        return {"content": [{"type": "text", "text": "Storage unavailable"}], "isError": True}

    work_item_id = params.get("work_item_id")
    lease_id = params.get("lease_id")
    evidence_digest = params.get("evidence_digest")
    test_summary = params.get("test_summary")
    commit_sha = params.get("commit_sha")

    if not work_item_id or not lease_id or not evidence_digest or not test_summary:
        return {"content": [{"type": "text", "text": "Missing required fields"}], "isError": True}

    try:
        current_item = store.load_work_item(work_item_id)
    except KeyError:
        return {"content": [{"type": "text", "text": f"Work item '{work_item_id}' not found"}], "isError": True}

    if current_item.lease_id != lease_id:
        return {"content": [{"type": "text", "text": f"Invalid lease_id for work item"}], "isError": True}

    service = WorkItemService(store)
    res = service.transition(
        work_item_id,
        status=WorkItemStatus.VERIFYING,
        actor=current_item.assigned_client_id or "agent",
        role=current_item.role,
        client_id=current_item.assigned_client_id or "agent",
        assigned_client_id=current_item.assigned_client_id,
        lease_id=current_item.lease_id,
        evidence_digest=evidence_digest,
        summary=f"Proof submitted: {test_summary}",
    )

    if not res.decision.allow:
        return {"content": [{"type": "text", "text": f"Transition denied: {res.decision.denials}"}], "isError": True}

    return {
        "content": [{
            "type": "text",
            "text": json.dumps({
                "status": "verifying",
                "work_item_id": work_item_id,
                "evidence_digest": evidence_digest,
                "work_item": res.work_item.model_dump(mode="json"),
            }, indent=2)
        }],
        "isError": False,
    }


def handle_work_intake_escalate(params: Dict[str, Any]) -> Dict[str, Any]:
    store = _get_store(params)
    if store is None:
        return {"content": [{"type": "text", "text": "Storage unavailable"}], "isError": True}

    work_item_id = params.get("work_item_id")
    lease_id = params.get("lease_id")
    reason = params.get("reason")

    if not work_item_id or not lease_id or not reason:
        return {"content": [{"type": "text", "text": "Missing required fields"}], "isError": True}

    try:
        current_item = store.load_work_item(work_item_id)
    except KeyError:
        return {"content": [{"type": "text", "text": f"Work item '{work_item_id}' not found"}], "isError": True}

    if current_item.lease_id != lease_id:
        return {"content": [{"type": "text", "text": f"Invalid lease_id"}], "isError": True}

    service = WorkItemService(store)
    res = service.transition(
        work_item_id,
        status=WorkItemStatus.BLOCKED,
        actor=current_item.assigned_client_id or "agent",
        role=current_item.role,
        client_id=current_item.assigned_client_id or "agent",
        blocked_reason=" ".join(reason.split())[:500],
        summary=f"Escalated blocker: {reason}",
    )

    return {
        "content": [{
            "type": "text",
            "text": json.dumps({
                "status": "blocked",
                "work_item_id": work_item_id,
                "blocked_reason": res.work_item.blocked_reason,
            }, indent=2)
        }],
        "isError": False,
    }


def _sync_comment_to_connector(work_item_id: str, comment: str) -> None:
    """Best-effort sync to external connector if work item maps to an external ticket."""
    reg = get_connector_registry()
    for conn in reg.list_connectors():
        if work_item_id.startswith(f"wi-{conn.config.platform.value}-"):
            remote_id = work_item_id[len(f"wi-{conn.config.platform.value}-"):]
            ref = ExternalWorkRef(
                platform=conn.config.platform,
                instance_url=conn.config.instance_url,
                remote_id=remote_id,
                remote_key=f"#{remote_id}",
            )
            try:
                conn.post_comment(ref, comment)
            except Exception:
                pass
