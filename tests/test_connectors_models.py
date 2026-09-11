from datetime import datetime, timezone
import pytest
from pydantic import ValidationError

from vad.connectors.models import (
    AttestationSyncPayload,
    ConnectorAuthType,
    ConnectorConfig,
    ConnectorPlatform,
    ExternalWorkRef,
    IngressEventRecord,
    OutboundTransitionRequest,
    WorkIntakePayload,
)


def test_external_work_ref_valid():
    ref = ExternalWorkRef(
        platform=ConnectorPlatform.GITHUB,
        instance_url="https://github.com",
        remote_id="12345",
        remote_key="#42",
        project_id="acme/telephony",
        web_url="https://github.com/acme/telephony/issues/42",
        remote_status="open",
        author="alice",
        metadata={"milestone": "v1.0"},
    )
    assert ref.platform == ConnectorPlatform.GITHUB
    assert ref.remote_key == "#42"
    assert ref.project_id == "acme/telephony"


def test_external_work_ref_rejects_control_characters():
    with pytest.raises(ValidationError):
        ExternalWorkRef(
            platform=ConnectorPlatform.JIRA,
            instance_url="https://jira.example.com",
            remote_id="10001\ninjection",
            remote_key="PROJ-123",
        )


def test_work_intake_payload_defaults():
    ref = ExternalWorkRef(
        platform=ConnectorPlatform.JIRA,
        instance_url="https://jira.example.com",
        remote_id="10001",
        remote_key="PROJ-123",
    )
    payload = WorkIntakePayload(
        ref=ref,
        title="Fix audio codec deadlock in SIP gateway",
        description="Steps to reproduce deadlock...",
        acceptance_criteria=["Must reproduce under 500 concurrent channels", "Zero dropped packets"],
    )
    assert payload.priority == 100
    assert payload.role == "developer"
    assert payload.effort_type == "feature"
    assert payload.mees_estimate == 80
    assert len(payload.acceptance_criteria) == 2


def test_connector_config_validation():
    cfg = ConnectorConfig(
        name="corp-jira",
        platform=ConnectorPlatform.JIRA,
        instance_url="https://corp.atlassian.net",
        auth_type=ConnectorAuthType.TOKEN,
        secret_token="secret-token-value",
        webhook_secret="webhook-hmac-secret",
        project_whitelist=["VOT", "SIP"],
    )
    assert cfg.enabled is True
    assert cfg.poll_interval_seconds == 300
    assert "VOT" in cfg.project_whitelist


def test_outbound_transition_request():
    ref = ExternalWorkRef(
        platform=ConnectorPlatform.GITLAB,
        instance_url="https://gitlab.example.com",
        remote_id="999",
        remote_key="!88",
    )
    req = OutboundTransitionRequest(
        ref=ref,
        target_status="closed",
        transition_note="Resolved by automated verifier",
        evidence_digest="a" * 64,
        approver_id="operator-42",
    )
    assert req.evidence_digest == "a" * 64
    assert req.target_status == "closed"


def test_attestation_sync_payload_digest_format():
    ref = ExternalWorkRef(
        platform=ConnectorPlatform.GITHUB,
        instance_url="https://github.com",
        remote_id="1",
        remote_key="#1",
    )
    # Invalid digest length
    with pytest.raises(ValidationError):
        AttestationSyncPayload(
            ref=ref,
            evidence_digest="short_digest",
            test_summary="Passed 5 tests",
        )

    # Valid digest
    attestation = AttestationSyncPayload(
        ref=ref,
        evidence_digest="b" * 64,
        test_summary="Passed all 14 contract checks",
        commit_sha="abcd1234efgh5678",
    )
    assert attestation.verified is True


def test_ingress_event_record():
    record = IngressEventRecord(
        event_id="evt-100",
        delivery_id="deliv-xyz",
        provider="github",
        payload={"action": "opened"},
    )
    assert record.processed is False
    assert record.delivery_id == "deliv-xyz"
