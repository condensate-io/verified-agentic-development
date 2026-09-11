from __future__ import annotations

import json
import uuid
from typing import Any, Dict, Optional, Tuple

from vad.connectors.models import WorkIntakePayload
from vad.connectors.webhook_receiver import IngressResult, WebhookReceiver
from vad.control_plane.work_items import WorkItem, WorkItemGovernance, WorkItemStatus
from vad.server.api.work_items import WorkItemService
from vad.server.db.store import ServerStore


class WebhookIngressService:
    """Service for handling webhook ingress and synchronizing work items into ServerStore."""

    def __init__(self, db: ServerStore, receiver: Optional[WebhookReceiver] = None) -> None:
        self.db = db
        self.receiver = receiver or WebhookReceiver()

    def handle_webhook(
        self,
        provider: str,
        headers: dict[str, str],
        body: bytes,
    ) -> Tuple[int, Dict[str, Any]]:
        result: IngressResult = self.receiver.process_webhook(provider, headers, body)

        if result.status == "unauthorized":
            return 401, {
                "error": "unauthorized",
                "message": result.message,
                "event_id": result.event_id,
            }

        if result.status == "error":
            return 400, {
                "error": "bad_request",
                "message": result.message,
                "event_id": result.event_id,
            }

        if result.status in ("duplicate", "ignored"):
            return 200, {
                "status": result.status,
                "message": result.message,
                "event_id": result.event_id,
                "delivery_id": result.delivery_id,
            }

        # Status is 'admitted'
        work_intake = result.work_intake
        if work_intake is None:
            return 200, {
                "status": "admitted",
                "message": result.message,
                "event_id": result.event_id,
            }

        created_item = self._provision_work_item(work_intake)
        return 201, {
            "status": "admitted",
            "message": result.message,
            "event_id": result.event_id,
            "delivery_id": result.delivery_id,
            "work_item": created_item.model_dump(mode="json"),
        }

    def _provision_work_item(self, intake: WorkIntakePayload) -> WorkItem:
        work_item_id = f"wi-{intake.ref.platform.value}-{intake.ref.remote_id.replace('/', '-')}"
        run_id = f"run-intake-{intake.ref.project_id or 'default'}".replace("/", "-")

        # Check if already exists in store
        try:
            return self.db.load_work_item(work_item_id)
        except KeyError:
            pass

        governance = WorkItemGovernance(
            effort_type=intake.effort_type,
            mees_estimate=intake.mees_estimate,
            token_budget=intake.token_budget,
            approval_required=(intake.mees_estimate < 50 or intake.high_risk),
            operator_intent_ref=intake.operator_intent_ref,
            high_risk=intake.high_risk,
        )

        clean_title = " ".join(intake.title.split())[:200]
        clean_desc = " ".join(intake.description.split())[:2000]

        work_item = WorkItem(
            work_item_id=work_item_id,
            run_id=run_id,
            title=clean_title,
            description=clean_desc,
            role=intake.role,
            requested_capability=intake.requested_capability,
            priority=intake.priority,
            status=WorkItemStatus.QUEUED,
            governance=governance,
        )

        service = WorkItemService(self.db)
        result = service.create(
            work_item,
            actor="webhook-ingress",
            role="operator",
            summary=f"Ingested from {intake.ref.platform.value} {intake.ref.remote_key}",
        )
        return result.work_item
