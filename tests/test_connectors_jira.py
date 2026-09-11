import pytest

from vad.connectors.jira import JiraConnector
from vad.connectors.models import (
    AttestationSyncPayload,
    ConnectorConfig,
    ConnectorPlatform,
    ExternalWorkRef,
    OutboundTransitionRequest,
)


@pytest.fixture
def jira_config():
    return ConnectorConfig(
        name="jira-vot",
        platform=ConnectorPlatform.JIRA,
        instance_url="https://five9.atlassian.net",
        secret_token="jira-shared-secret-12345",
        project_whitelist=["VOT", "SIP"],
        label_filter=["agent-task", "vad-ready"],
    )


def test_jira_webhook_secret_validation(jira_config):
    conn = JiraConnector(jira_config)
    assert conn.validate_webhook({"X-Atlassian-Webhook-Secret": "jira-shared-secret-12345"}, b"") is True
    assert conn.validate_webhook({"Authorization": "Bearer jira-shared-secret-12345"}, b"") is True
    assert conn.validate_webhook({"X-Atlassian-Webhook-Secret": "wrong"}, b"") is False
    assert conn.validate_webhook({}, b"") is False


def test_jira_parse_issue_created_plain_text(jira_config):
    conn = JiraConnector(jira_config)
    payload = {
        "webhookEvent": "jira:issue_created",
        "issue": {
            "id": "10401",
            "key": "VOT-420",
            "fields": {
                "summary": "Implement Opus DTX comfort noise packets",
                "description": "Ensure DTX frames are properly injected.\n- [ ] Parse SID frames\n- [ ] Maintain jitter buffer sync",
                "project": {"key": "VOT"},
                "priority": {"name": "High"},
                "labels": ["vad-ready"],
                "creator": {"displayName": "Bob Architect"},
                "status": {"name": "To Do"},
                "customfield_repo": "Five9/voice-codec",
                "customfield_mees": 85,
            },
        },
    }

    result = conn.parse_webhook_event({}, payload)
    assert result is not None
    assert result.title == "Implement Opus DTX comfort noise packets"
    assert result.ref.remote_key == "VOT-420"
    assert result.priority == 180  # High priority mapped to 180
    assert result.target_repo == "Five9/voice-codec"
    assert result.mees_estimate == 85
    assert len(result.acceptance_criteria) == 2


def test_jira_adf_conversion():
    adf = {
        "version": 1,
        "type": "doc",
        "content": [
            {
                "type": "paragraph",
                "content": [{"type": "text", "text": "This is an ADF description with details."}],
            }
        ],
    }
    markdown = JiraConnector.adf_to_markdown(adf)
    assert "This is an ADF description with details." in markdown

    # Markdown to ADF
    rendered_adf = JiraConnector.markdown_to_adf("Line 1\nLine 2")
    assert rendered_adf["type"] == "doc"
    assert len(rendered_adf["content"]) == 2
    assert rendered_adf["content"][0]["content"][0]["text"] == "Line 1"


def test_jira_parse_issue_with_adf_description(jira_config):
    conn = JiraConnector(jira_config)
    payload = {
        "webhookEvent": "jira:issue_updated",
        "issue": {
            "id": "10402",
            "key": "SIP-99",
            "fields": {
                "summary": "Fix TLS handshake retransmissions",
                "description": {
                    "version": 1,
                    "type": "doc",
                    "content": [
                        {
                            "type": "paragraph",
                            "content": [{"type": "text", "text": "AC: Verify TLS 1.3 resumption"}],
                        }
                    ],
                },
                "project": {"key": "SIP"},
                "labels": ["agent-task"],
            },
        },
    }
    result = conn.parse_webhook_event({}, payload)
    assert result is not None
    assert "AC: Verify TLS 1.3 resumption" in result.description
    assert len(result.acceptance_criteria) == 1
    assert result.acceptance_criteria[0] == "Verify TLS 1.3 resumption"


def test_jira_attestation_and_status_sync(jira_config):
    conn = JiraConnector(jira_config)
    ref = ExternalWorkRef(
        platform=ConnectorPlatform.JIRA,
        instance_url="https://five9.atlassian.net",
        remote_id="10401",
        remote_key="VOT-420",
        project_id="VOT",
    )
    attestation = AttestationSyncPayload(
        ref=ref,
        evidence_digest="e" * 64,
        test_summary="Passed all 24 DTX codec tests",
    )
    assert conn.attach_attestation(attestation) is True
    assert len(conn.posted_comments) == 1
    assert "VAD Verification Attestation" in conn.posted_comments[0]["comment"]
    assert "adf" in conn.posted_comments[0]

    trans = OutboundTransitionRequest(
        ref=ref,
        target_status="completed",
        transition_note="Completed and verified by VAD Release Guardian",
        evidence_digest="e" * 64,
    )
    assert conn.sync_status(trans) is True
    assert len(conn.status_transitions) == 1
    assert conn.status_transitions[0]["remote_target_status"] == "Done"
