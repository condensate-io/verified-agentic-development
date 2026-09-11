import pytest
from typing import Any

from vad.connectors.base import BaseWorkConnector
from vad.connectors.models import (
    AttestationSyncPayload,
    ConnectorConfig,
    ConnectorPlatform,
    ExternalWorkRef,
    OutboundTransitionRequest,
    WorkIntakePayload,
)


class DummyConnector(BaseWorkConnector):
    def validate_webhook(self, headers: dict[str, str], body: bytes) -> bool:
        normalized = self.normalize_headers(headers)
        return normalized.get("x-dummy-token") == self.config.secret_token

    def parse_webhook_event(
        self, headers: dict[str, str], payload: dict[str, Any]
    ) -> WorkIntakePayload | None:
        if payload.get("event") != "ticket_created":
            return None
        data = payload.get("data", {})
        return WorkIntakePayload(
            ref=ExternalWorkRef(
                platform=self.config.platform,
                instance_url=self.config.instance_url,
                remote_id=str(data.get("id")),
                remote_key=f"DUMMY-{data.get('id')}",
                project_id=data.get("project"),
            ),
            title=data.get("title", "Untitled"),
            description=data.get("desc", ""),
            labels=data.get("tags", []),
        )

    def poll_work_items(self, query: str | None = None) -> list[WorkIntakePayload]:
        return []

    def sync_status(self, transition: OutboundTransitionRequest) -> bool:
        return True

    def post_comment(
        self, ref: ExternalWorkRef, comment: str, is_internal: bool = False
    ) -> bool:
        return True

    def attach_attestation(self, attestation: AttestationSyncPayload) -> bool:
        return True


def test_base_connector_header_normalization():
    cfg = ConnectorConfig(
        name="test-conn",
        platform=ConnectorPlatform.GENERIC,
        instance_url="https://generic.example.com",
        secret_token="secret-123",
    )
    conn = DummyConnector(cfg)
    headers = {"X-Dummy-Token": "secret-123", "Content-Type": "application/json"}
    assert conn.validate_webhook(headers, b"") is True
    assert conn.validate_webhook({"X-Dummy-Token": "wrong"}, b"") is False


def test_base_connector_project_whitelist():
    cfg = ConnectorConfig(
        name="test-conn",
        platform=ConnectorPlatform.GENERIC,
        instance_url="https://generic.example.com",
        project_whitelist=["alpha", "beta"],
    )
    conn = DummyConnector(cfg)
    assert conn.is_project_allowed("alpha") is True
    assert conn.is_project_allowed("gamma") is False
    assert conn.is_project_allowed(None) is False

    # Empty whitelist allows any project
    open_cfg = ConnectorConfig(
        name="open-conn",
        platform=ConnectorPlatform.GENERIC,
        instance_url="https://generic.example.com",
    )
    open_conn = DummyConnector(open_cfg)
    assert open_conn.is_project_allowed("anything") is True
    assert open_conn.is_project_allowed(None) is True


def test_base_connector_label_filter():
    cfg = ConnectorConfig(
        name="test-conn",
        platform=ConnectorPlatform.GENERIC,
        instance_url="https://generic.example.com",
        label_filter=["ai-ready", "vad"],
    )
    conn = DummyConnector(cfg)
    assert conn.matches_labels(["AI-READY", "bug"]) is True
    assert conn.matches_labels(["unrelated"]) is False

    # Empty filter matches everything
    open_cfg = ConnectorConfig(
        name="open-conn",
        platform=ConnectorPlatform.GENERIC,
        instance_url="https://generic.example.com",
    )
    open_conn = DummyConnector(open_cfg)
    assert open_conn.matches_labels(["random"]) is True


def test_base_connector_health_check():
    cfg = ConnectorConfig(
        name="test-conn",
        platform=ConnectorPlatform.GENERIC,
        instance_url="https://generic.example.com",
    )
    conn = DummyConnector(cfg)
    health = conn.health_check()
    assert health["name"] == "test-conn"
    assert health["healthy"] is True
