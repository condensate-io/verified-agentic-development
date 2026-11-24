from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

ALLOWED_SECRET_REF_PREFIXES = ("env:", "file:", "vault:", "kms:")
_RAW_SECRET_MARKERS = (
    "sk-",
    "ghp_",
    "glpat-",
    "xoxb-",
    "bearer ",
    "api_key=",
    "token=",
    "password=",
    "secret=",
)


class MemoryProviderKind(str, Enum):
    NULL = "null"
    LOCAL = "local"
    CONDENSATE_CORE = "condensate_core"
    MEM0 = "mem0"
    GRAPHITI = "graphiti"
    CUSTOM_HTTP = "custom_http"
    CUSTOM_MCP = "custom_mcp"


class MemoryTransport(str, Enum):
    IN_PROCESS = "in_process"
    HTTP = "http"
    MCP = "mcp"
    BOTH = "both"


class MemoryProviderConfig(BaseModel):
    provider: MemoryProviderKind = MemoryProviderKind.NULL
    transport: MemoryTransport = MemoryTransport.IN_PROCESS
    endpoint_url: str | None = None
    mcp_endpoint: str | None = None
    local_path: str | None = None
    project_id: str = "default"
    auth_secret_ref: str | None = None
    timeout_seconds: float = Field(default=5.0, gt=0.0, le=120.0)
    fail_open_on_unavailable: bool = True
    extra_endpoints: dict[str, str] = Field(default_factory=dict)
    options: dict[str, Any] = Field(default_factory=dict)

    @field_validator("auth_secret_ref")
    @classmethod
    def validate_auth_secret_ref(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
            raise ValueError("auth_secret_ref must not contain control characters")
        stripped = value.strip()
        if not stripped:
            raise ValueError("auth_secret_ref must not be empty")
        lowered = stripped.lower()
        if any(marker in lowered for marker in _RAW_SECRET_MARKERS):
            raise ValueError("auth_secret_ref must reference a secret store, not contain a raw secret")
        if not stripped.startswith(ALLOWED_SECRET_REF_PREFIXES):
            raise ValueError(
                "auth_secret_ref must start with one of: env:, file:, vault:, kms:"
            )
        _, _, target = stripped.partition(":")
        if not target.strip():
            raise ValueError("auth_secret_ref must include a non-empty reference after the prefix")
        return stripped

    @model_validator(mode="after")
    def validate_provider_endpoints(self) -> "MemoryProviderConfig":
        has_http = bool(self.endpoint_url and self.endpoint_url.strip())
        has_mcp = bool(self.mcp_endpoint and self.mcp_endpoint.strip())

        if self.provider == MemoryProviderKind.CONDENSATE_CORE:
            if self.transport == MemoryTransport.HTTP and not has_http:
                raise ValueError("condensate_core with transport='http' requires endpoint_url")
            if self.transport == MemoryTransport.MCP and not has_mcp:
                raise ValueError("condensate_core with transport='mcp' requires mcp_endpoint")
            if self.transport == MemoryTransport.BOTH and not (has_http or has_mcp):
                raise ValueError(
                    "condensate_core with transport='both' requires endpoint_url or mcp_endpoint"
                )
        elif self.provider == MemoryProviderKind.CUSTOM_HTTP and not has_http:
            raise ValueError("custom_http provider requires endpoint_url")
        elif self.provider == MemoryProviderKind.CUSTOM_MCP and not has_mcp:
            raise ValueError("custom_mcp provider requires mcp_endpoint")

        return self
