"""SIEM Export Adapters: Transforms VAD audit events for Splunk, Datadog, Elastic, and Azure Sentinel."""

from abc import ABC, abstractmethod
from enum import Enum
import json
from typing import Any, Dict, List, Optional
import httpx
from pydantic import BaseModel, Field
from vad.audit.local_audit import AuditEvent


class SIEMProvider(str, Enum):
    SPLUNK = "splunk"
    DATADOG = "datadog"
    ELASTIC = "elastic"
    SENTINEL = "sentinel"


class SIEMMappingConfig(BaseModel):
    provider: SIEMProvider
    endpoint_url: str
    token: Optional[str] = None
    source_type: str = "vad:audit"
    index_name: str = "ai_governance"


class SIEMAdapter(ABC):
    """Abstract base class for transforming and exporting audit events to enterprise SIEM targets."""

    def __init__(self, config: SIEMMappingConfig):
        self.config = config

    @abstractmethod
    def format_event(self, event: AuditEvent) -> Dict[str, Any]:
        """Transforms a VAD AuditEvent into the vendor-specific payload format."""
        pass

    def export_batch(self, events: List[AuditEvent], client: Optional[httpx.Client] = None) -> bool:
        if not events:
            return True

        formatted = [self.format_event(e) for e in events]
        headers = {"Content-Type": "application/json"}
        if self.config.token:
            headers["Authorization"] = f"Bearer {self.config.token}"

        try:
            if client is not None:
                resp = client.post(self.config.endpoint_url, json=formatted, headers=headers)
                return resp.status_code in (200, 201, 202)
            with httpx.Client(timeout=5.0, headers=headers) as c:
                resp = c.post(self.config.endpoint_url, json=formatted)
                return resp.status_code in (200, 201, 202)
        except Exception:
            return False


class SplunkHECAdapter(SIEMAdapter):
    """Formats events for Splunk HTTP Event Collector (HEC)."""

    def format_event(self, event: AuditEvent) -> Dict[str, Any]:
        return {
            "sourcetype": self.config.source_type,
            "index": self.config.index_name,
            "event": event.model_dump(),
        }


class DatadogLogsAdapter(SIEMAdapter):
    """Formats events for Datadog Logs API intake."""

    def format_event(self, event: AuditEvent) -> Dict[str, Any]:
        return {
            "ddsource": "vad",
            "service": "agentic-os",
            "hostname": "vad-runtime",
            "message": f"[{event.action}] Agent={event.agent_id} Outcome={event.outcome}",
            "audit": event.model_dump(),
        }


class ElasticBulkAdapter(SIEMAdapter):
    """Formats events for Elasticsearch Bulk / Index API."""

    def format_event(self, event: AuditEvent) -> Dict[str, Any]:
        return {
            "_index": self.config.index_name,
            "_source": event.model_dump(),
        }


class SentinelDCEAdapter(SIEMAdapter):
    """Formats events for Azure Monitor / Sentinel Data Collection Endpoint (DCE)."""

    def format_event(self, event: AuditEvent) -> Dict[str, Any]:
        data = event.model_dump()
        data["TimeGenerated"] = event.timestamp
        return data


class SIEMExportFactory:
    """Factory generating configured SIEM adapters."""

    @classmethod
    def create(cls, config: SIEMMappingConfig) -> SIEMAdapter:
        if config.provider == SIEMProvider.SPLUNK:
            return SplunkHECAdapter(config)
        elif config.provider == SIEMProvider.DATADOG:
            return DatadogLogsAdapter(config)
        elif config.provider == SIEMProvider.ELASTIC:
            return ElasticBulkAdapter(config)
        elif config.provider == SIEMProvider.SENTINEL:
            return SentinelDCEAdapter(config)
        raise ValueError(f"Unsupported SIEM provider: {config.provider}")
