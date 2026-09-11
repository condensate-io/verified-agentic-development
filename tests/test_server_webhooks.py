import hashlib
import hmac
import json
import pytest
from pathlib import Path

from vad.connectors.github import GitHubConnector
from vad.connectors.models import ConnectorConfig, ConnectorPlatform
from vad.connectors.registry import get_connector_registry, reset_connector_registry
from vad.control_plane.work_items import WorkItemStatus
from vad.server.app import VADApi
from vad.server.db.store import ServerStore


@pytest.fixture
def server_env(tmp_path):
    reset_connector_registry()
    db_path = tmp_path / "test_server.db"
    evidence_root = tmp_path / "evidence"
    evidence_root.mkdir()

    # Register GitHub connector
    reg = get_connector_registry()
    cfg = ConnectorConfig(
        name="github",
        platform=ConnectorPlatform.GITHUB,
        instance_url="https://github.com",
        webhook_secret="top-secret-999",
        project_whitelist=["org/telephony"],
    )
    conn = GitHubConnector(cfg)
    reg.register(conn)

    api = VADApi(evidence_root=evidence_root, db_path=db_path)
    return api, db_path


def test_server_webhook_ingress_admitted(server_env):
    api, db_path = server_env
    payload = {
        "action": "opened",
        "issue": {
            "id": 555,
            "number": 42,
            "title": "Fix buffer overrun in SIP header parser",
            "body": "Long Via header crashes parser.\n- [ ] Add bounds check\n- [ ] Fuzz test with AFL",
            "state": "open",
        },
        "repository": {"full_name": "org/telephony"},
    }
    raw_body = json.dumps(payload).encode("utf-8")
    sig = "sha256=" + hmac.new(b"top-secret-999", raw_body, hashlib.sha256).hexdigest()
    headers = {
        "X-Hub-Signature-256": sig,
        "X-GitHub-Event": "issues",
        "X-GitHub-Delivery": "delivery-srv-1",
    }

    status_code, resp = api.handle(
        "POST",
        "/api/v1/webhooks/github",
        body=payload,
        headers=headers,
        raw_body=raw_body,
    )
    assert status_code == 201
    assert resp["status"] == "admitted"
    assert "work_item" in resp
    assert resp["work_item"]["title"] == "Fix buffer overrun in SIP header parser"
    assert resp["work_item"]["status"] == WorkItemStatus.QUEUED.value

    # Verify persisted in SQLite store
    store = ServerStore(db_path)
    wi = store.load_work_item(resp["work_item"]["work_item_id"])
    assert wi is not None
    assert wi.title == "Fix buffer overrun in SIP header parser"


def test_server_webhook_ingress_unauthorized(server_env):
    api, _ = server_env
    payload = {"action": "opened"}
    raw_body = json.dumps(payload).encode("utf-8")
    headers = {
        "X-Hub-Signature-256": "sha256=invalid",
        "X-GitHub-Event": "issues",
    }
    status_code, resp = api.handle(
        "POST",
        "/webhooks/github",
        body=payload,
        headers=headers,
        raw_body=raw_body,
    )
    assert status_code == 401
    assert resp["error"] == "unauthorized"


def test_server_webhook_ingress_duplicate(server_env):
    api, _ = server_env
    payload = {
        "action": "opened",
        "issue": {"id": 666, "number": 43, "title": "Second issue"},
        "repository": {"full_name": "org/telephony"},
    }
    raw_body = json.dumps(payload).encode("utf-8")
    sig = "sha256=" + hmac.new(b"top-secret-999", raw_body, hashlib.sha256).hexdigest()
    headers = {
        "X-Hub-Signature-256": sig,
        "X-GitHub-Event": "issues",
        "X-GitHub-Delivery": "delivery-dup-test",
    }

    # First attempt
    status_code1, resp1 = api.handle(
        "POST",
        "/api/v1/webhooks/github",
        body=payload,
        headers=headers,
        raw_body=raw_body,
    )
    assert status_code1 == 201

    # Second attempt with identical delivery ID
    status_code2, resp2 = api.handle(
        "POST",
        "/api/v1/webhooks/github",
        body=payload,
        headers=headers,
        raw_body=raw_body,
    )
    assert status_code2 == 200
    assert resp2["status"] == "duplicate"
