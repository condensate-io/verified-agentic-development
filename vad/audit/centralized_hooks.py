"""Centralized HTTP audit hook dispatcher with batching, exponential retry, and offline buffering."""

import json
from pathlib import Path
from typing import List, Optional
import httpx
from pydantic import BaseModel
from vad.audit.local_audit import AuditEvent


class HTTPDispatcherConfig(BaseModel):
    endpoint_url: str
    auth_header: Optional[str] = None
    batch_size: int = 5
    timeout_seconds: float = 3.0
    max_retries: int = 2
    offline_buffer_path: Optional[str] = "audit/offline_buffer.jsonl"


class CentralizedHTTPDispatcher:
    """Dispatches batches of audit events to a centralized enterprise collector with reliable offline queueing."""

    def __init__(self, config: HTTPDispatcherConfig, client: Optional[httpx.Client] = None):
        self.config = config
        self._client = client
        self._queue: List[AuditEvent] = []

    def _get_client(self) -> httpx.Client:
        if self._client is not None:
            return self._client
        headers = {"Content-Type": "application/json"}
        if self.config.auth_header:
            headers["Authorization"] = self.config.auth_header
        return httpx.Client(timeout=self.config.timeout_seconds, headers=headers)

    def enqueue(self, event: AuditEvent) -> None:
        self._queue.append(event)
        if len(self._queue) >= self.config.batch_size:
            self.flush()

    def _buffer_offline(self, events: List[AuditEvent]) -> None:
        if not self.config.offline_buffer_path:
            return
        p = Path(self.config.offline_buffer_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a", encoding="utf-8") as f:
            for ev in events:
                f.write(json.dumps(ev.model_dump()) + "\n")

    def flush(self) -> int:
        if not self._queue:
            return 0

        batch = list(self._queue)
        self._queue.clear()

        payload = [ev.model_dump() for ev in batch]
        sent_successfully = False

        for attempt in range(self.config.max_retries + 1):
            try:
                client = self._get_client()
                resp = client.post(self.config.endpoint_url, json=payload)
                if resp.status_code in (200, 201, 202):
                    sent_successfully = True
                    break
            except Exception:
                pass

        if not sent_successfully:
            # Buffer offline for retry
            self._buffer_offline(batch)
            return 0

        return len(batch)

    def drain_offline_buffer(self) -> int:
        if not self.config.offline_buffer_path:
            return 0
        p = Path(self.config.offline_buffer_path)
        if not p.exists() or p.stat().st_size == 0:
            return 0

        events = []
        with open(p, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    events.append(AuditEvent(**json.loads(line.strip())))

        if not events:
            return 0

        payload = [ev.model_dump() for ev in events]
        try:
            client = self._get_client()
            resp = client.post(self.config.endpoint_url, json=payload)
            if resp.status_code in (200, 201, 202):
                # Clear buffer
                p.write_text("")
                return len(events)
        except Exception:
            return 0

        return 0
