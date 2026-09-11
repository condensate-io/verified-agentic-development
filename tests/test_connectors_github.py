import hashlib
import hmac
import json
import pytest

from vad.connectors.github import GitHubConnector
from vad.connectors.models import (
    AttestationSyncPayload,
    ConnectorConfig,
    ConnectorPlatform,
    ExternalWorkRef,
    OutboundTransitionRequest,
)
from vad.connectors.registry import get_connector_registry


@pytest.fixture
def github_config():
    return ConnectorConfig(
        name="gh-vot",
        platform=ConnectorPlatform.GITHUB,
        instance_url="https://github.com",
        webhook_secret="super-secret-token",
        project_whitelist=["Five9/voice-platform", "Five9/sip-router"],
        label_filter=["ai-task", "vad-ready"],
    )


def test_github_webhook_hmac_validation(github_config):
    conn = GitHubConnector(github_config)
    body = b'{"action":"opened"}'
    valid_sig = "sha256=" + hmac.new(b"super-secret-token", body, hashlib.sha256).hexdigest()

    # Valid signature
    assert conn.validate_webhook({"X-Hub-Signature-256": valid_sig}, body) is True

    # Invalid signature
    assert conn.validate_webhook({"X-Hub-Signature-256": "sha256=badbadbad"}, body) is False

    # Missing header
    assert conn.validate_webhook({}, body) is False


def test_github_parse_issues_event(github_config):
    conn = GitHubConnector(github_config)
    payload = {
        "action": "opened",
        "issue": {
            "id": 888123,
            "number": 104,
            "title": "Fix SIP CANCEL packet race condition",
            "body": "## Description\nRace condition when CANCEL arrives before INVITE.\n\n- [ ] Reproduce with SIPp\n- [ ] Patch sip_fsm.c\n- [ ] Verify zero memory leaks",
            "labels": [{"name": "vad-ready"}, {"name": "bug"}],
            "html_url": "https://github.com/Five9/voice-platform/issues/104",
            "state": "open",
            "user": {"login": "dev-alice"},
        },
        "repository": {
            "full_name": "Five9/voice-platform",
        },
    }

    result = conn.parse_webhook_event({"X-GitHub-Event": "issues"}, payload)
    assert result is not None
    assert result.title == "Fix SIP CANCEL packet race condition"
    assert result.ref.remote_key == "#104"
    assert result.ref.author == "dev-alice"
    assert len(result.acceptance_criteria) == 3
    assert result.acceptance_criteria[0] == "Reproduce with SIPp"
    assert result.target_repo == "Five9/voice-platform"
    assert result.is_pull_request is False


def test_github_parse_issues_filtered_by_label(github_config):
    conn = GitHubConnector(github_config)
    payload = {
        "action": "opened",
        "issue": {
            "id": 888124,
            "number": 105,
            "title": "Unrelated issue",
            "labels": [{"name": "documentation"}],  # Does not match label_filter
        },
        "repository": {"full_name": "Five9/voice-platform"},
    }
    assert conn.parse_webhook_event({"X-GitHub-Event": "issues"}, payload) is None


def test_github_parse_pull_request_event(github_config):
    conn = GitHubConnector(github_config)
    payload = {
        "action": "opened",
        "pull_request": {
            "id": 999123,
            "number": 55,
            "title": "Add opus codec negotiation",
            "body": "Implements RFC 7587 for opus codec.",
            "labels": [{"name": "ai-task"}],
            "html_url": "https://github.com/Five9/sip-router/pull/55",
            "state": "open",
            "user": {"login": "dev-bob"},
            "head": {"ref": "feature/opus-codec", "sha": "1122334455667788"},
        },
        "repository": {
            "full_name": "Five9/sip-router",
        },
    }

    result = conn.parse_webhook_event({"X-GitHub-Event": "pull_request"}, payload)
    assert result is not None
    assert result.is_pull_request is True
    assert result.branch == "feature/opus-codec"
    assert result.ref.metadata["commit_sha"] == "1122334455667788"


def test_github_attach_attestation_and_sync(github_config):
    conn = GitHubConnector(github_config)
    ref = ExternalWorkRef(
        platform=ConnectorPlatform.GITHUB,
        instance_url="https://github.com",
        remote_id="888123",
        remote_key="#104",
        project_id="Five9/voice-platform",
    )
    attestation = AttestationSyncPayload(
        ref=ref,
        evidence_digest="c" * 64,
        test_summary="Passed 48 unit tests and 3 sanitizers",
        commit_sha="fedcba9876543210",
    )
    assert conn.attach_attestation(attestation) is True
    assert len(conn.posted_comments) == 1
    assert "VAD Verification Attestation" in conn.posted_comments[0]["comment"]
    assert "c" * 64 in conn.posted_comments[0]["comment"]

    # Test status sync
    trans = OutboundTransitionRequest(
        ref=ref,
        target_status="completed",
        transition_note="Closed by VAD Release Guardian",
        evidence_digest="c" * 64,
    )
    assert conn.sync_status(trans) is True
    assert len(conn.status_transitions) == 1
