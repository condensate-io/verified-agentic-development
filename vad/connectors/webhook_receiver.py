from __future__ import annotations

import collections
import hashlib
import json
import uuid
from typing import Any, Dict, Optional

from pydantic import BaseModel

from vad.connectors.base import BaseWorkConnector
from vad.connectors.models import (
    ConnectorPlatform,
    IngressEventRecord,
    WorkIntakePayload,
)
from vad.connectors.registry import ConnectorRegistry, get_connector_registry


class IngressResult(BaseModel):
    status: str  # "admitted", "duplicate", "unauthorized", "ignored", "error"
    message: str
    work_intake: Optional[WorkIntakePayload] = None
    event_id: str
    delivery_id: Optional[str] = None


class WebhookReceiver:
    """Core ingress processor for validating, deduplicating, and canonicalizing incoming webhooks."""

    def __init__(
        self,
        registry: Optional[ConnectorRegistry] = None,
        max_seen_deliveries: int = 10000,
    ) -> None:
        self.registry = registry or get_connector_registry()
        self.seen_deliveries: collections.deque[str] = collections.deque(maxlen=max_seen_deliveries)
        self.event_records: list[IngressEventRecord] = []

    def process_webhook(
        self,
        provider: str,
        headers: dict[str, str],
        body: bytes,
    ) -> IngressResult:
        event_id = f"evt-{uuid.uuid4().hex[:12]}"
        normalized_headers = {k.lower(): v for k, v in headers.items()}

        # Extract delivery ID for deduplication
        delivery_id = (
            normalized_headers.get("x-github-delivery")
            or normalized_headers.get("x-gitlab-event-uuid")
            or normalized_headers.get("x-delivery-id")
            or normalized_headers.get("x-request-id")
            or hashlib.sha256(body).hexdigest()
        )

        # 1. Replay Guard
        if delivery_id in self.seen_deliveries:
            return IngressResult(
                status="duplicate",
                message=f"Duplicate delivery ID '{delivery_id}' dropped by replay guard",
                event_id=event_id,
                delivery_id=delivery_id,
            )

        # 2. Locate Connector
        connector = self._resolve_connector(provider)
        if connector is None:
            return IngressResult(
                status="error",
                message=f"No active connector found for provider '{provider}'",
                event_id=event_id,
                delivery_id=delivery_id,
            )

        # 3. Cryptographic Validation
        if not connector.validate_webhook(headers, body):
            return IngressResult(
                status="unauthorized",
                message="Invalid webhook signature, secret token, or missing credentials",
                event_id=event_id,
                delivery_id=delivery_id,
            )

        # Mark delivery ID as seen once validated
        self.seen_deliveries.append(delivery_id)

        # 4. Parse JSON Payload
        try:
            payload = json.loads(body.decode("utf-8")) if body else {}
        except Exception as e:
            return IngressResult(
                status="error",
                message=f"Malformed JSON payload: {e}",
                event_id=event_id,
                delivery_id=delivery_id,
            )

        # 5. Parse Event
        work_intake = connector.parse_webhook_event(headers, payload)
        if work_intake is None:
            return IngressResult(
                status="ignored",
                message="Webhook event parsed but ignored by connector filter/action rules",
                event_id=event_id,
                delivery_id=delivery_id,
            )

        # Record ingress event
        record = IngressEventRecord(
            event_id=event_id,
            delivery_id=delivery_id,
            provider=provider,
            payload=payload,
            processed=True,
        )
        self.event_records.append(record)

        return IngressResult(
            status="admitted",
            message="Webhook authenticated and canonicalized successfully",
            work_intake=work_intake,
            event_id=event_id,
            delivery_id=delivery_id,
        )

    def _resolve_connector(self, provider: str) -> Optional[BaseWorkConnector]:
        # First check active connector by name
        conn = self.registry.get(provider)
        if conn is not None:
            return conn

        # Next check by platform enum
        try:
            platform = ConnectorPlatform(provider.lower())
            connectors = self.registry.get_by_platform(platform)
            if connectors:
                return connectors[0]
        except ValueError:
            pass

        return None
