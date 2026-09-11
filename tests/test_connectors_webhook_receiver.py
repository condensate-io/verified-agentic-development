import hashlib
import hmac
import json
import pytest

from vad.connectors.github import GitHubConnector
from vad.connectors.models import ConnectorConfig, ConnectorPlatform
from vad.connectors.registry import ConnectorRegistry
from vad.connectors.webhook_receiver import WebhookReceiver


@pytest.fixture
def configured_receiver():
    reg = ConnectorRegistry()
    reg.register_connector_class(ConnectorPlatform.GITHUB, GitHubConnector)

    cfg = ConnectorConfig(
        name="gh-production",
        platform=ConnectorPlatform.GITHUB,
        instance_url="https://github.com",
        webhook_secret="receiver-secret-999",
    )
    conn = reg.create_connector(cfg)
    reg.register(conn)
    return WebhookReceiver(registry=reg)


def test_webhook_receiver_admitted_flow(configured_receiver):
    payload_dict = {
        "action": "opened",
        "issue": {
            "id": 111,
            "number": 1,
            "title": "Ingest live audio stream into RTP queue",
            "body": "- [ ] Validate G.711 packets\n- [ ] Buffer jitter",
            "state": "open",
        },
        "repository": {"full_name": "Five9/voice-gateway"},
    }
    body = json.dumps(payload_dict).encode("utf-8")
    sig = "sha256=" + hmac.new(b"receiver-secret-999", body, hashlib.sha256).hexdigest()

    headers = {
        "X-Hub-Signature-256": sig,
        "X-GitHub-Event": "issues",
        "X-GitHub-Delivery": "delivery-unique-001",
    }

    result = configured_receiver.process_webhook("github", headers, body)
    assert result.status == "admitted"
    assert result.work_intake is not None
    assert result.work_intake.title == "Ingest live audio stream into RTP queue"
    assert result.delivery_id == "delivery-unique-001"
    assert len(configured_receiver.event_records) == 1


def test_webhook_receiver_replay_guard(configured_receiver):
    payload_dict = {"action": "opened", "issue": {"id": 111, "number": 1, "title": "Test"}}
    body = json.dumps(payload_dict).encode("utf-8")
    sig = "sha256=" + hmac.new(b"receiver-secret-999", body, hashlib.sha256).hexdigest()

    headers = {
        "X-Hub-Signature-256": sig,
        "X-GitHub-Event": "issues",
        "X-GitHub-Delivery": "delivery-replay-test",
    }

    res1 = configured_receiver.process_webhook("github", headers, body)
    assert res1.status == "admitted"

    # Second request with same delivery ID must be dropped as duplicate
    res2 = configured_receiver.process_webhook("github", headers, body)
    assert res2.status == "duplicate"
    assert "dropped by replay guard" in res2.message


def test_webhook_receiver_unauthorized_rejection(configured_receiver):
    body = b'{"action":"opened"}'
    headers = {
        "X-Hub-Signature-256": "sha256=invalid-signature",
        "X-GitHub-Event": "issues",
        "X-GitHub-Delivery": "delivery-bad-sig",
    }
    result = configured_receiver.process_webhook("github", headers, body)
    assert result.status == "unauthorized"


def test_webhook_receiver_unknown_provider(configured_receiver):
    result = configured_receiver.process_webhook("unknown-provider", {}, b"")
    assert result.status == "error"
    assert "No active connector found" in result.message
