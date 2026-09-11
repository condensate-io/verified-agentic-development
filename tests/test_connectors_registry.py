import os
import pytest

from vad.connectors.base import BaseWorkConnector
from vad.connectors.models import (
    AttestationSyncPayload,
    ConnectorConfig,
    ConnectorPlatform,
    ExternalWorkRef,
    OutboundTransitionRequest,
    WorkIntakePayload,
)
from vad.connectors.registry import (
    ConnectorRegistry,
    get_connector_registry,
    reset_connector_registry,
)


class MockGitHubConnector(BaseWorkConnector):
    def validate_webhook(self, headers: dict[str, str], body: bytes) -> bool:
        return True

    def parse_webhook_event(self, headers: dict[str, str], payload: dict) -> WorkIntakePayload | None:
        return None

    def poll_work_items(self, query: str | None = None) -> list[WorkIntakePayload]:
        return []

    def sync_status(self, transition: OutboundTransitionRequest) -> bool:
        return True

    def post_comment(self, ref: ExternalWorkRef, comment: str, is_internal: bool = False) -> bool:
        return True

    def attach_attestation(self, attestation: AttestationSyncPayload) -> bool:
        return True


def test_registry_registration_and_lookup():
    reg = ConnectorRegistry()
    reg.register_connector_class(ConnectorPlatform.GITHUB, MockGitHubConnector)

    cfg = ConnectorConfig(
        name="gh-prod",
        platform=ConnectorPlatform.GITHUB,
        instance_url="https://github.com",
        secret_token="tok-123",
    )
    conn = reg.create_connector(cfg)
    assert isinstance(conn, MockGitHubConnector)
    assert conn.config.name == "gh-prod"

    reg.register(conn)
    assert reg.get("gh-prod") is conn
    assert len(reg.list_connectors()) == 1
    assert len(reg.get_by_platform(ConnectorPlatform.GITHUB)) == 1

    reg.unregister("gh-prod")
    assert reg.get("gh-prod") is None


def test_registry_unregistered_platform_raises():
    reg = ConnectorRegistry()
    cfg = ConnectorConfig(
        name="jira-main",
        platform=ConnectorPlatform.JIRA,
        instance_url="https://jira.corp",
    )
    with pytest.raises(ValueError, match="No connector implementation registered"):
        reg.create_connector(cfg)


def test_registry_env_secret_fallback(monkeypatch):
    reg = ConnectorRegistry()
    reg.register_connector_class(ConnectorPlatform.GITHUB, MockGitHubConnector)
    monkeypatch.setenv("VAD_GITHUB_TOKEN", "env-secret-token")
    monkeypatch.setenv("VAD_GITHUB_WEBHOOK_SECRET", "env-webhook-secret")

    cfg = ConnectorConfig(
        name="gh-auto",
        platform=ConnectorPlatform.GITHUB,
        instance_url="https://github.com",
    )
    conn = reg.create_connector(cfg)
    assert conn.config.secret_token == "env-secret-token"
    assert conn.config.webhook_secret == "env-webhook-secret"


def test_global_singleton():
    reset_connector_registry()
    r1 = get_connector_registry()
    r2 = get_connector_registry()
    assert r1 is r2
