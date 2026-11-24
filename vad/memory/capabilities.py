from __future__ import annotations

from pydantic import BaseModel, Field

from vad.memory.contracts import MEMORY_INTERFACE_VERSION


class MemoryCapabilities(BaseModel):
    interface_version: str = MEMORY_INTERFACE_VERSION
    fact_storage: bool = True
    lesson_storage: bool = True
    scoped_query: bool = True
    health_check: bool = True
    semantic_search: bool = False
    causal_graph: bool = False
    contradiction_resolution: bool = False
    context_consolidation: bool = False
    provenance_traversal: bool = False
    supported_transports: list[str] = Field(default_factory=lambda: ["in_process"])


class ProviderHealth(BaseModel):
    healthy: bool
    provider_name: str
    latency_ms: float | None = None
    detail: str = ""
