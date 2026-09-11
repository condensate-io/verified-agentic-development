import pytest

from vad.connectors.gitlab import GitLabConnector
from vad.connectors.models import (
    AttestationSyncPayload,
    ConnectorConfig,
    ConnectorPlatform,
    ExternalWorkRef,
    OutboundTransitionRequest,
)


@pytest.fixture
def gitlab_config():
    return ConnectorConfig(
        name="gl-vot",
        platform=ConnectorPlatform.GITLAB,
        instance_url="https://gitlab.five9.com",
        secret_token="gitlab-webhook-secret-token",
        project_whitelist=["telephony/sip-proxy", "telephony/rtp-engine"],
        label_filter=["ai-task", "vad-ready"],
    )


def test_gitlab_webhook_token_validation(gitlab_config):
    conn = GitLabConnector(gitlab_config)
    assert conn.validate_webhook({"X-Gitlab-Token": "gitlab-webhook-secret-token"}, b"") is True
    assert conn.validate_webhook({"X-Gitlab-Token": "wrong-token"}, b"") is False
    assert conn.validate_webhook({}, b"") is False


def test_gitlab_parse_issue_event(gitlab_config):
    conn = GitLabConnector(gitlab_config)
    payload = {
        "object_kind": "issue",
        "user": {"username": "gitlab-dev"},
        "project": {"path_with_namespace": "telephony/sip-proxy"},
        "object_attributes": {
            "id": 501,
            "iid": 12,
            "title": "Fix SIP INVITE timeout handling",
            "description": "## Acceptance Criteria\n- [ ] Add 32s timer B\n- [ ] Clean up dialog state",
            "state": "opened",
            "action": "open",
            "url": "https://gitlab.five9.com/telephony/sip-proxy/-/issues/12",
        },
        "labels": [{"title": "vad-ready"}],
    }
    result = conn.parse_webhook_event({"X-Gitlab-Event": "Issue Hook"}, payload)
    assert result is not None
    assert result.title == "Fix SIP INVITE timeout handling"
    assert result.ref.remote_key == "#12"
    assert result.target_repo == "telephony/sip-proxy"
    assert len(result.acceptance_criteria) == 2
    assert result.is_pull_request is False


def test_gitlab_parse_merge_request_event(gitlab_config):
    conn = GitLabConnector(gitlab_config)
    payload = {
        "object_kind": "merge_request",
        "user": {"username": "mr-author"},
        "project": {"path_with_namespace": "telephony/rtp-engine"},
        "object_attributes": {
            "id": 801,
            "iid": 45,
            "title": "Support SRTP AES-GCM 256",
            "description": "Upgrades encryption suites",
            "state": "opened",
            "action": "open",
            "source_branch": "feature/srtp-gcm",
            "last_commit": {"id": "abcdef0123456789"},
            "url": "https://gitlab.five9.com/telephony/rtp-engine/-/merge_requests/45",
        },
        "labels": [{"title": "ai-task"}],
    }
    result = conn.parse_webhook_event({"X-Gitlab-Event": "Merge Request Hook"}, payload)
    assert result is not None
    assert result.is_pull_request is True
    assert result.ref.remote_key == "!45"
    assert result.branch == "feature/srtp-gcm"


def test_gitlab_attestation_and_status_sync(gitlab_config):
    conn = GitLabConnector(gitlab_config)
    ref = ExternalWorkRef(
        platform=ConnectorPlatform.GITLAB,
        instance_url="https://gitlab.five9.com",
        remote_id="501",
        remote_key="#12",
        project_id="telephony/sip-proxy",
    )
    attestation = AttestationSyncPayload(
        ref=ref,
        evidence_digest="d" * 64,
        test_summary="Passed 31 SIP functional checks",
        commit_sha="aabbccddeeff0011",
    )
    assert conn.attach_attestation(attestation) is True
    assert len(conn.posted_comments) == 1
    assert "GitLab VAD Verification Attestation" in conn.posted_comments[0]["comment"]

    trans = OutboundTransitionRequest(
        ref=ref,
        target_status="closed",
        transition_note="Merged by Release Guardian",
        evidence_digest="d" * 64,
    )
    assert conn.sync_status(trans) is True
    assert len(conn.status_transitions) == 1
