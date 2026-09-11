from pathlib import Path
from unittest.mock import MagicMock
import httpx
import pytest
from vad.plugins.claude_code.hooks.notification import NotificationHook


def test_notification_hook_logs_locally(tmp_path):
    log_file = tmp_path / "alerts.jsonl"
    hook = NotificationHook(log_file=str(log_file))

    payload = {
        "title": "HITL Required",
        "message": "Push requires confirmation",
        "level": "warning",
    }
    res = hook.process(payload)
    assert res["status"] == "dispatched"
    assert res["level"] == "warning"

    assert log_file.exists()
    content = log_file.read_text()
    assert "HITL Required" in content


def test_notification_hook_forwards_to_webhook(tmp_path):
    log_file = tmp_path / "alerts.jsonl"
    mock_client = MagicMock(spec=httpx.Client)

    hook = NotificationHook(
        log_file=str(log_file),
        webhook_url="http://127.0.0.1:9090/webhook",
        client=mock_client,
    )

    payload = {
        "title": "Build Failed",
        "message": "Task B7 encountered syntax error",
        "level": "error",
    }
    hook.process(payload)
    assert mock_client.post.call_count == 1
