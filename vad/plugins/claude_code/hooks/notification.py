"""Notification Hook: Forwards notifications and alerts to the Operator TUI and optional webhooks."""

from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from typing import Any, Dict, Optional
import httpx
from pydantic import BaseModel, Field


class NotificationPayload(BaseModel):
    session_id: str = "default-session"
    title: str
    message: str
    level: str = "info"  # "info", "warning", "error", "alert"
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    metadata: Dict[str, Any] = Field(default_factory=dict)


class NotificationHook:
    """Claude Code Notification hook broadcasting events to the Operator TUI and webhook receivers."""

    def __init__(
        self,
        log_file: str = "notifications/alerts.jsonl",
        webhook_url: Optional[str] = None,
        client: Optional[httpx.Client] = None,
    ):
        self.log_file = log_file
        self.webhook_url = webhook_url
        self._client = client

    def process(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        notif = NotificationPayload(**payload)

        # 1. Append to local notifications log for Operator TUI consumption
        try:
            p = Path(self.log_file)
            p.parent.mkdir(parents=True, exist_ok=True)
            with open(p, "a", encoding="utf-8") as f:
                f.write(json.dumps(notif.model_dump()) + "\n")
        except Exception:
            pass

        # 2. Forward to external webhook if configured
        if self.webhook_url:
            try:
                if self._client:
                    self._client.post(self.webhook_url, json=notif.model_dump())
                else:
                    with httpx.Client(timeout=2.0) as c:
                        c.post(self.webhook_url, json=notif.model_dump())
            except Exception:
                pass

        return {"status": "dispatched", "level": notif.level}


def main(argv: Optional[list] = None) -> int:
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
    except Exception:
        payload = {}

    hook = NotificationHook()
    res = hook.process(payload)
    print(json.dumps(res))
    return 0


if __name__ == "__main__":
    sys.exit(main())
