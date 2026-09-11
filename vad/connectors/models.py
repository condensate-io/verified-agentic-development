from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ConnectorPlatform(str, Enum):
    GITHUB = "github"
    GITLAB = "gitlab"
    JIRA = "jira"
    LINEAR = "linear"
    ASANA = "asana"
    MONDAY = "monday"
    AZURE_DEVOPS = "azure_devops"
    GENERIC = "generic"


class ConnectorAuthType(str, Enum):
    TOKEN = "token"
    BEARER = "bearer"
    BASIC = "basic"
    WEBHOOK_HMAC = "webhook_hmac"
    NONE = "none"


class ExternalWorkRef(BaseModel):
    platform: ConnectorPlatform
    instance_url: str = Field(min_length=1, max_length=500)
    remote_id: str = Field(min_length=1, max_length=200)
    remote_key: str = Field(min_length=1, max_length=200)
    project_id: str | None = Field(default=None, max_length=200)
    web_url: str | None = Field(default=None, max_length=1000)
    remote_status: str | None = Field(default=None, max_length=100)
    author: str | None = Field(default=None, max_length=150)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("remote_id", "remote_key", "project_id")
    @classmethod
    def identifiers_must_be_safe(cls, value: str | None) -> str | None:
        if value is None:
            return value
        if any(separator in value for separator in ["\n", "\r", "\t"]):
            raise ValueError("external work identifiers must not contain control characters")
        return value


class WorkIntakePayload(BaseModel):
    ref: ExternalWorkRef
    title: str = Field(min_length=1, max_length=300)
    description: str = Field(default="", max_length=50000)
    acceptance_criteria: list[str] = Field(default_factory=list)
    priority: int = Field(default=100, ge=0, le=1000)
    role: str = Field(default="developer", min_length=1, max_length=80)
    requested_capability: str | None = Field(default=None, max_length=120)
    effort_type: str = Field(default="feature", min_length=1, max_length=80)
    mees_estimate: int = Field(default=80, ge=0, le=100)
    labels: list[str] = Field(default_factory=list)
    target_repo: str | None = Field(default=None, max_length=500)
    branch: str | None = Field(default=None, max_length=200)
    is_pull_request: bool = False
    raw_event_type: str | None = Field(default=None, max_length=100)
    token_budget: int = Field(default=100000, ge=0)
    operator_intent_ref: str | None = Field(default=None, max_length=200)
    high_risk: bool = False

    @field_validator("title", "role", "effort_type")
    @classmethod
    def fields_must_be_clean(cls, value: str) -> str:
        if any(separator in value for separator in ["\n", "\r", "\t"]):
            raise ValueError("field must not contain control characters")
        return value.strip()


class ConnectorConfig(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    platform: ConnectorPlatform
    instance_url: str = Field(min_length=1, max_length=500)
    enabled: bool = True
    auth_type: ConnectorAuthType = ConnectorAuthType.TOKEN
    secret_token: str | None = Field(default=None, max_length=500)
    username: str | None = Field(default=None, max_length=200)
    webhook_secret: str | None = Field(default=None, max_length=500)
    default_role: str = Field(default="developer", max_length=80)
    project_whitelist: list[str] = Field(default_factory=list)
    label_filter: list[str] = Field(default_factory=list)
    poll_interval_seconds: int = Field(default=300, ge=10, le=86400)
    auto_admit: bool = False


class OutboundTransitionRequest(BaseModel):
    ref: ExternalWorkRef
    target_status: str = Field(min_length=1, max_length=100)
    transition_note: str | None = Field(default=None, max_length=5000)
    labels_to_add: list[str] = Field(default_factory=list)
    labels_to_remove: list[str] = Field(default_factory=list)
    evidence_digest: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    attestation_ref: str | None = Field(default=None, max_length=200)
    approver_id: str | None = Field(default=None, max_length=160)


class AttestationSyncPayload(BaseModel):
    ref: ExternalWorkRef
    evidence_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    test_summary: str = Field(min_length=1, max_length=2000)
    status: str = Field(default="verified", max_length=50)
    verified: bool = True
    commit_sha: str | None = Field(default=None, max_length=100)
    diff_url: str | None = Field(default=None, max_length=1000)
    raw_evidence: dict[str, Any] = Field(default_factory=dict)


class IngressEventRecord(BaseModel):
    event_id: str = Field(min_length=1, max_length=160)
    delivery_id: str | None = Field(default=None, max_length=160)
    provider: str = Field(min_length=1, max_length=80)
    timestamp: datetime = Field(default_factory=_utc_now)
    payload: dict[str, Any] = Field(default_factory=dict)
    processed: bool = False
    work_item_id: str | None = Field(default=None, max_length=160)
    error: str | None = Field(default=None, max_length=1000)
