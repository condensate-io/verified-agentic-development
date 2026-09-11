import pytest

from vad.connectors.generic import GenericWebhookConnector
from vad.connectors.models import (
    AttestationSyncPayload,
    ConnectorConfig,
    ConnectorPlatform,
    ExternalWorkRef,
    OutboundTransitionRequest,
)


@pytest.fixture
def generic_config():
    return ConnectorConfig(
        name="custom-webhook",
        platform=ConnectorPlatform.GENERIC,
        instance_url="https://hooks.example.com",
        secret_token="webhook-secret-xyz",
        project_whitelist=["alpha", "beta"],
        label_filter=["governed", "vad"],
    )


def test_generic_webhook_secret_validation(generic_config):
    conn = GenericWebhookConnector(generic_config)
    assert conn.validate_webhook({"X-Webhook-Token": "webhook-secret-xyz"}, b"") is True
    assert conn.validate_webhook({"Authorization": "Bearer webhook-secret-xyz"}, b"") is True
    assert conn.validate_webhook({"X-Webhook-Token": "bad"}, b"") is False
    assert conn.validate_webhook({}, b"") is False


def test_generic_parse_standard_json(generic_config):
    conn = GenericWebhookConnector(generic_config)
    payload = {
        "id": "TICK-77",
        "title": "Migrate DB schema to PostgreSQL 16",
        "description": "Upgrade required.\n- [ ] Run pg_upgrade\n- [ ] Update connection pool",
        "project": "alpha",
        "labels": ["governed", "database"],
        "priority": 120,
    }
    result = conn.parse_webhook_event({}, payload)
    assert result is not None
    assert result.title == "Migrate DB schema to PostgreSQL 16"
    assert result.ref.remote_key == "GEN-TICK-77"
    assert result.priority == 120
    assert len(result.acceptance_criteria) == 2


def test_generic_parse_linear_issue():
    cfg = ConnectorConfig(
        name="linear-conn",
        platform=ConnectorPlatform.LINEAR,
        instance_url="https://linear.app",
        secret_token="lin-secret",
    )
    conn = GenericWebhookConnector(cfg)
    payload = {
        "action": "create",
        "type": "Issue",
        "data": {
            "id": "lin-909",
            "identifier": "ENG-101",
            "title": "Fix WebRTC ICE candidate gathering timeout",
            "description": "Gathering takes >15s on mobile networks.\n- [ ] Add STUN server fallback",
            "priority": 1,
            "team": {"key": "ENG"},
            "state": {"name": "In Progress"},
            "labels": [{"name": "vad"}],
        },
    }
    result = conn.parse_webhook_event({}, payload)
    assert result is not None
    assert result.ref.platform == ConnectorPlatform.LINEAR
    assert result.ref.remote_key == "ENG-101"
    assert result.title == "Fix WebRTC ICE candidate gathering timeout"
    assert len(result.acceptance_criteria) == 1


def test_generic_attestation_and_sync(generic_config):
    conn = GenericWebhookConnector(generic_config)
    ref = ExternalWorkRef(
        platform=ConnectorPlatform.GENERIC,
        instance_url="https://hooks.example.com",
        remote_id="TICK-77",
        remote_key="GEN-TICK-77",
        project_id="alpha",
    )
    attestation = AttestationSyncPayload(
        ref=ref,
        evidence_digest="f" * 64,
        test_summary="Passed schema migration check",
    )
    assert conn.attach_attestation(attestation) is True
    assert len(conn.posted_comments) == 1
    assert "f" * 64 in conn.posted_comments[0]["comment"]

    trans = OutboundTransitionRequest(
        ref=ref,
        target_status="completed",
        transition_note="Schema migration verified in container",
        evidence_digest="f" * 64,
    )
    assert conn.sync_status(trans) is True
    assert len(conn.status_transitions) == 1
