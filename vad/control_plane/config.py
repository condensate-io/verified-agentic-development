from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, ValidationInfo, field_validator, model_validator


LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}
SECRET_MARKERS = ("api_key=", "token=", "password=", "secret=", "private_key", "sk-")
LOG_LEVELS = {"debug", "info", "warning", "error"}


class ControlPlaneConfig(BaseModel):
    config_path: Path = Path(".vad/control-plane/config.json")
    bind_host: str = "127.0.0.1"
    port: int = Field(default=8080, ge=0, le=65535)
    db_path: Path = Path(".vad/control-plane/vad.sqlite3")
    evidence_root: Path = Path(".vad/control-plane/evidence")
    ui_root: Path = Path(".vad/control-plane/ui")
    plugin_root: Path = Path(".vad/control-plane/plugins")
    log_level: str = "info"
    allow_non_local_bind: bool = False

    @classmethod
    def default_config_path(cls, base_dir: str | Path = ".") -> Path:
        return Path(base_dir) / ".vad" / "control-plane" / "config.json"

    @classmethod
    def from_env(cls, env: Mapping[str, str], **overrides: Any) -> "ControlPlaneConfig":
        values: dict[str, Any] = {}
        env_map = {
            "VAD_CONTROL_PLANE_CONFIG": ("config_path", Path),
            "VAD_CONTROL_PLANE_HOST": ("bind_host", str),
            "VAD_CONTROL_PLANE_PORT": ("port", int),
            "VAD_CONTROL_PLANE_DB": ("db_path", Path),
            "VAD_CONTROL_PLANE_EVIDENCE_ROOT": ("evidence_root", Path),
            "VAD_CONTROL_PLANE_UI_ROOT": ("ui_root", Path),
            "VAD_CONTROL_PLANE_PLUGIN_ROOT": ("plugin_root", Path),
            "VAD_CONTROL_PLANE_LOG_LEVEL": ("log_level", str),
            "VAD_CONTROL_PLANE_ALLOW_NON_LOCAL_BIND": ("allow_non_local_bind", parse_bool),
        }
        for env_key, (field_name, converter) in env_map.items():
            if env_key in env:
                values[field_name] = converter(env[env_key])
        values.update(overrides)
        return cls(**values)

    @field_validator("config_path", "db_path", "evidence_root", "ui_root", "plugin_root")
    @classmethod
    def path_must_not_contain_secret_marker(cls, value: Path) -> Path:
        _reject_secret_value(str(value))
        return value

    @field_validator("bind_host", "log_level")
    @classmethod
    def text_must_not_contain_secret_marker(cls, value: str, info: ValidationInfo) -> str:
        if not value.strip():
            raise ValueError(f"{info.field_name} must not be empty")
        _reject_secret_value(value)
        return value

    @field_validator("log_level")
    @classmethod
    def log_level_must_be_supported(cls, value: str) -> str:
        lowered = value.lower()
        if lowered not in LOG_LEVELS:
            raise ValueError(f"log_level must be one of: {', '.join(sorted(LOG_LEVELS))}")
        return lowered

    @model_validator(mode="after")
    def non_local_bind_requires_explicit_opt_in(self) -> "ControlPlaneConfig":
        if self.bind_host not in LOCAL_HOSTS and not self.allow_non_local_bind:
            raise ValueError("non-local control-plane bind requires allow_non_local_bind=True")
        return self

    def non_local_bind_warning(self) -> str | None:
        if self.bind_host in LOCAL_HOSTS:
            return None
        return f"control plane will bind to non-local host {self.bind_host}; use only on trusted local networks"


def parse_bool(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"invalid boolean value: {value}")


def _reject_secret_value(value: str) -> None:
    lowered = value.lower()
    if any(marker in lowered for marker in SECRET_MARKERS):
        raise ValueError("control-plane config must not contain secret values")
