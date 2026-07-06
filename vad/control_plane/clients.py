from __future__ import annotations

from enum import Enum
from pathlib import Path
from datetime import datetime, timezone

from pydantic import BaseModel, Field, field_validator


class ClientType(str, Enum):
    CODEX = "codex"
    CLAUDE_CODE = "claude_code"
    VS_CODE = "vscode"
    ANTIGRAVITY = "antigravity"
    WINDSURF = "windsurf"
    CURSOR = "cursor"
    OPENCODE = "opencode"
    GENERIC_MCP = "generic_mcp"
    OTHER = "other"


class ClientConnectionMode(str, Enum):
    MCP = "mcp"
    PLUGIN = "plugin"
    SDK = "sdk"
    CLI = "cli"
    HTTP = "http"


class ClientTrustState(str, Enum):
    UNTRUSTED = "untrusted"
    TRUSTED = "trusted"
    QUARANTINED = "quarantined"


class ClientRuntimeStatus(str, Enum):
    ACTIVE = "active"
    STALE = "stale"
    DISCONNECTED = "disconnected"


class ClientManifest(BaseModel):
    client_id: str = Field(min_length=1, max_length=160)
    display_name: str = Field(min_length=1, max_length=160)
    client_type: ClientType
    version: str = Field(min_length=1, max_length=80)
    connection_mode: ClientConnectionMode
    supported_capabilities: tuple[str, ...] = Field(min_length=1)
    workspace_root: Path
    trust_state: ClientTrustState = ClientTrustState.UNTRUSTED
    requested_policy_permissions: tuple[str, ...] = ()

    @field_validator("client_id")
    @classmethod
    def client_id_must_be_safe(cls, value: str) -> str:
        if any(separator in value for separator in ["/", "\\", "\n", "\r", "\t"]):
            raise ValueError("client_id must not contain path or control separators")
        return value

    @field_validator("version", "display_name")
    @classmethod
    def text_fields_must_not_contain_controls(cls, value: str) -> str:
        if any(separator in value for separator in ["\n", "\r", "\t"]):
            raise ValueError("manifest text fields must not contain control separators")
        return value

    @field_validator("supported_capabilities")
    @classmethod
    def capabilities_must_be_safe(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(value.strip() for value in values)
        if any(not value for value in normalized):
            raise ValueError("supported capabilities must not be empty")
        if any(any(separator in value for separator in ["/", "\\", "\n", "\r", "\t"]) for value in normalized):
            raise ValueError("supported capabilities must not contain path or control separators")
        return tuple(sorted(set(normalized)))

    @field_validator("workspace_root")
    @classmethod
    def workspace_root_must_be_local_path(cls, value: Path) -> Path:
        text = str(value)
        if any(separator in text for separator in ["\n", "\r", "\t"]):
            raise ValueError("workspace_root must not contain control separators")
        return value

    @field_validator("requested_policy_permissions")
    @classmethod
    def capabilities_do_not_grant_policy_permissions(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if values:
            raise ValueError("client capabilities do not grant policy permissions")
        return values

    @classmethod
    def json_schema(cls) -> dict:
        return cls.model_json_schema()


class ClientHeartbeat(BaseModel):
    client_id: str = Field(min_length=1, max_length=160)
    run_id: str | None = Field(default=None, max_length=160)
    task_id: str | None = Field(default=None, max_length=160)
    actor: str = Field(min_length=1, max_length=160)
    role: str = Field(min_length=1, max_length=80)
    status: ClientRuntimeStatus = ClientRuntimeStatus.ACTIVE
    observed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    summary: str = Field(default="Client heartbeat.", min_length=1, max_length=300)

    @field_validator("client_id", "run_id", "task_id", "actor", "role")
    @classmethod
    def identifiers_must_be_safe(cls, value: str | None) -> str | None:
        if value is None:
            return value
        if any(separator in value for separator in ["/", "\\", "\n", "\r", "\t"]):
            raise ValueError("client heartbeat identifiers must not contain path or control separators")
        return value

    @field_validator("summary")
    @classmethod
    def summary_must_not_contain_controls(cls, value: str) -> str:
        if any(separator in value for separator in ["\n", "\r", "\t"]):
            raise ValueError("client heartbeat summary must not contain control separators")
        return value


class ClientStatusSnapshot(BaseModel):
    manifest: ClientManifest
    status: ClientRuntimeStatus = ClientRuntimeStatus.DISCONNECTED
    last_heartbeat_at: datetime | None = None
    last_run_id: str | None = None
    last_task_id: str | None = None
    lost_task_leases: tuple[str, ...] = ()
