from __future__ import annotations

import os
from typing import Dict, List, Optional, Type

from vad.connectors.base import BaseWorkConnector
from vad.connectors.models import (
    ConnectorAuthType,
    ConnectorConfig,
    ConnectorPlatform,
)


class ConnectorRegistry:
    """Registry and factory for managing external work intake connectors."""

    def __init__(self) -> None:
        self._connector_classes: Dict[ConnectorPlatform, Type[BaseWorkConnector]] = {}
        self._active_connectors: Dict[str, BaseWorkConnector] = {}

    def register_connector_class(
        self, platform: ConnectorPlatform, cls: Type[BaseWorkConnector]
    ) -> None:
        """Registers a connector class implementation for a given platform."""
        self._connector_classes[platform] = cls

    def unregister_connector_class(self, platform: ConnectorPlatform) -> None:
        self._connector_classes.pop(platform, None)

    def create_connector(self, config: ConnectorConfig) -> BaseWorkConnector:
        """Creates and configures a connector instance using registered classes."""
        # Resolve secrets from environment if not present in config
        resolved_config = self._resolve_env_secrets(config)
        cls = self._connector_classes.get(resolved_config.platform)
        if cls is None:
            raise ValueError(
                f"No connector implementation registered for platform '{resolved_config.platform.value}'"
            )
        return cls(resolved_config)

    def register(self, connector: BaseWorkConnector) -> None:
        """Registers an active connector instance by its config name."""
        self._active_connectors[connector.config.name] = connector

    def unregister(self, name: str) -> Optional[BaseWorkConnector]:
        """Removes an active connector by name."""
        return self._active_connectors.pop(name, None)

    def get(self, name: str) -> Optional[BaseWorkConnector]:
        """Retrieves an active connector by name."""
        return self._active_connectors.get(name)

    def get_by_platform(self, platform: ConnectorPlatform) -> List[BaseWorkConnector]:
        """Retrieves all active connectors matching a given platform."""
        return [
            c for c in self._active_connectors.values()
            if c.config.platform == platform and c.config.enabled
        ]

    def list_connectors(self) -> List[BaseWorkConnector]:
        """Returns all registered active connectors."""
        return list(self._active_connectors.values())

    def clear(self) -> None:
        """Clears all active connectors and class registrations."""
        self._active_connectors.clear()
        self._connector_classes.clear()

    @staticmethod
    def _resolve_env_secrets(config: ConnectorConfig) -> ConnectorConfig:
        data = config.model_dump()
        prefix = f"VAD_{config.platform.value.upper()}"
        if not data.get("secret_token"):
            data["secret_token"] = os.environ.get(f"{prefix}_TOKEN") or os.environ.get(f"{prefix}_API_KEY")
        if not data.get("webhook_secret"):
            data["webhook_secret"] = os.environ.get(f"{prefix}_WEBHOOK_SECRET")
        return ConnectorConfig(**data)


# Global singleton instance
_GLOBAL_REGISTRY = ConnectorRegistry()


def get_connector_registry() -> ConnectorRegistry:
    return _GLOBAL_REGISTRY


def reset_connector_registry() -> None:
    _GLOBAL_REGISTRY.clear()
