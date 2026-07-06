from enum import Enum
import re

from pydantic import BaseModel, Field, field_validator, model_validator


class DeploymentEnvironment(str, Enum):
    DEVELOPMENT = "development"
    STAGING = "staging"
    PRODUCTION = "production"


class DeploymentStrategy(str, Enum):
    DRY_RUN = "dry-run"
    BLUE_GREEN = "blue-green"
    CANARY = "canary"
    ROLLING = "rolling"


class SecretReference(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    ref: str = Field(min_length=1, max_length=300)

    @field_validator("ref")
    @classmethod
    def ref_must_be_external_reference(cls, value: str) -> str:
        _reject_inline_secret(value)
        if "://" not in value and not value.startswith(("env:", "secret:", "vault:", "aws-sm:", "gcp-sm:", "azure-kv:")):
            raise ValueError("secret ref must use an explicit secret reference scheme")
        return value


class TelemetryRequirement(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    query: str = Field(min_length=1, max_length=500)
    minimum_health: float = Field(ge=0.0, le=1.0)
    window_seconds: int = Field(gt=0, le=86400)


class RollbackPolicy(BaseModel):
    enabled: bool = True
    strategy: DeploymentStrategy = DeploymentStrategy.BLUE_GREEN
    trigger_metric: str = Field(min_length=1, max_length=120)
    threshold: float = Field(ge=0.0, le=1.0)
    max_wait_seconds: int = Field(gt=0, le=86400)

    @model_validator(mode="after")
    def rollback_requires_reversible_strategy(self):
        if self.enabled and self.strategy == DeploymentStrategy.DRY_RUN:
            raise ValueError("rollback strategy must be reversible")
        return self


class DeploymentTarget(BaseModel):
    target_id: str = Field(min_length=1, max_length=120)
    environment: DeploymentEnvironment
    provider: str = Field(min_length=1, max_length=80)
    region: str | None = Field(default=None, max_length=80)
    strategy: DeploymentStrategy
    artifact_digest: str = Field(min_length=64, max_length=64)
    endpoint: str | None = Field(default=None, max_length=300)
    secret_refs: list[SecretReference] = Field(default_factory=list)
    telemetry: list[TelemetryRequirement] = Field(default_factory=list)
    rollback: RollbackPolicy

    @field_validator("artifact_digest")
    @classmethod
    def artifact_digest_must_be_sha256_hex(cls, value: str) -> str:
        if not re.fullmatch(r"[0-9a-f]{64}", value):
            raise ValueError("artifact_digest must be lowercase SHA-256 hex")
        return value

    @field_validator("endpoint", "provider", "region")
    @classmethod
    def target_fields_must_not_contain_secrets(cls, value: str | None) -> str | None:
        if value is not None:
            _reject_inline_secret(value)
        return value

    @model_validator(mode="after")
    def production_requires_telemetry_and_rollback(self):
        if self.environment == DeploymentEnvironment.PRODUCTION:
            if not self.telemetry:
                raise ValueError("production deployment target requires telemetry")
            if not self.rollback.enabled:
                raise ValueError("production deployment target requires rollback")
        return self


class DeploymentPlan(BaseModel):
    plan_id: str = Field(min_length=1, max_length=120)
    target: DeploymentTarget
    approval_ref: str | None = Field(default=None, max_length=300)
    evidence_ref: str | None = Field(default=None, max_length=300)

    @field_validator("approval_ref", "evidence_ref")
    @classmethod
    def refs_must_not_contain_secrets(cls, value: str | None) -> str | None:
        if value is not None:
            _reject_inline_secret(value)
        return value


def _reject_inline_secret(value: str) -> None:
    lowered = value.lower()
    secret_markers = ("api_key=", "token=", "password=", "secret=", "private_key")
    if any(marker in lowered for marker in secret_markers):
        raise ValueError("inline secret values are not allowed; use secret references")
    if re.search(r"sk-[A-Za-z0-9_-]{16,}", value):
        raise ValueError("inline secret values are not allowed; use secret references")
