from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from vad.connectors.models import (
    AttestationSyncPayload,
    ConnectorConfig,
    ExternalWorkRef,
    OutboundTransitionRequest,
    WorkIntakePayload,
)


class BaseWorkConnector(ABC):
    """Abstract base class for all work intake and external issue/ticket connectors."""

    def __init__(self, config: ConnectorConfig) -> None:
        self.config = config

    @abstractmethod
    def validate_webhook(self, headers: dict[str, str], body: bytes) -> bool:
        """Cryptographically verifies incoming webhook authenticity (e.g. HMAC-SHA256, secret token)."""
        pass

    @abstractmethod
    def parse_webhook_event(
        self, headers: dict[str, str], payload: dict[str, Any]
    ) -> WorkIntakePayload | None:
        """Parses external webhook payload into canonical WorkIntakePayload, or None if event ignored."""
        pass

    @abstractmethod
    def poll_work_items(self, query: str | None = None) -> list[WorkIntakePayload]:
        """Polls remote system for open/assigned work items matching connector criteria."""
        pass

    @abstractmethod
    def sync_status(self, transition: OutboundTransitionRequest) -> bool:
        """Transitions external ticket/issue status (e.g. In Progress, Verifying, Done)."""
        pass

    @abstractmethod
    def post_comment(
        self, ref: ExternalWorkRef, comment: str, is_internal: bool = False
    ) -> bool:
        """Posts a progress comment or note on the remote ticket."""
        pass

    @abstractmethod
    def attach_attestation(self, attestation: AttestationSyncPayload) -> bool:
        """Attaches verification proof digest, commit SHA, and test summary to remote item."""
        pass

    def health_check(self) -> dict[str, Any]:
        """Performs connectivity and authentication health check."""
        return {
            "name": self.config.name,
            "platform": self.config.platform.value,
            "instance_url": self.config.instance_url,
            "enabled": self.config.enabled,
            "healthy": True,
        }

    def is_project_allowed(self, project_id: str | None) -> bool:
        """Checks if remote project is permitted by whitelist (allow all if list is empty)."""
        if not self.config.project_whitelist:
            return True
        if project_id is None:
            return False
        return project_id in self.config.project_whitelist

    def matches_labels(self, labels: list[str]) -> bool:
        """Checks if remote item matches required labels (allow all if filter is empty)."""
        if not self.config.label_filter:
            return True
        return any(fl.lower() in [l.lower() for l in labels] for fl in self.config.label_filter)

    @staticmethod
    def normalize_headers(headers: dict[str, str]) -> dict[str, str]:
        """Returns lowercase-keyed header dict for case-insensitive lookup."""
        return {k.lower(): v for k, v in headers.items()}
