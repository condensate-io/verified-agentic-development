from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from pydantic import ValidationError

from vad.connectors.models import ConnectorConfig
from vad.connectors.registry import ConnectorRegistry, get_connector_registry
from vad.server.api.webhooks import WebhookIngressService
from vad.server.db.store import ServerStore


class ConnectorsAdminService:
    """Administration and management service for external work connectors."""

    def __init__(
        self,
        db: Optional[ServerStore] = None,
        registry: Optional[ConnectorRegistry] = None,
    ) -> None:
        self.db = db
        self.registry = registry or get_connector_registry()

    def list_connectors(self) -> Tuple[int, Dict[str, Any]]:
        connectors = self.registry.list_connectors()
        data = [c.health_check() for c in connectors]
        return 200, {"connectors": data, "count": len(data)}

    def register_connector(self, payload: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
        try:
            config = ConnectorConfig(**payload)
        except ValidationError as e:
            return 400, {"error": "validation_error", "detail": e.errors()}

        try:
            connector = self.registry.create_connector(config)
            self.registry.register(connector)
        except Exception as e:
            return 400, {"error": "registration_failed", "message": str(e)}

        return 201, {
            "status": "registered",
            "connector": connector.health_check(),
        }

    def unregister_connector(self, name: str) -> Tuple[int, Dict[str, Any]]:
        removed = self.registry.unregister(name)
        if removed is None:
            return 404, {"error": "not_found", "message": f"Connector '{name}' not found"}
        return 200, {"status": "unregistered", "name": name}

    def check_health(self, name: str) -> Tuple[int, Dict[str, Any]]:
        connector = self.registry.get(name)
        if connector is None:
            return 404, {"error": "not_found", "message": f"Connector '{name}' not found"}
        return 200, {"health": connector.health_check()}

    def trigger_sync(self, name: str) -> Tuple[int, Dict[str, Any]]:
        connector = self.registry.get(name)
        if connector is None:
            return 404, {"error": "not_found", "message": f"Connector '{name}' not found"}

        items = connector.poll_work_items()
        admitted_count = 0
        if self.db is not None and items:
            ingress = WebhookIngressService(self.db)
            for item in items:
                ingress._provision_work_item(item)
                admitted_count += 1

        return 200, {
            "status": "sync_completed",
            "name": name,
            "polled_count": len(items),
            "admitted_count": admitted_count,
        }
