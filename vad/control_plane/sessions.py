from __future__ import annotations

import base64
import hashlib
import hmac
import json
from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field, field_validator


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _normalize_time(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _safe_identifier(value: str) -> str:
    if any(separator in value for separator in ["/", "\\", "\n", "\r", "\t"]):
        raise ValueError("session identifiers must not contain path or control separators")
    return value


class HostRole(str, Enum):
    BUILDER = "builder"
    VERIFIER = "verifier"
    RELEASE_GUARDIAN = "release_guardian"
    OPERATOR = "operator"


_ROLE_ALLOWED_ACTIONS: dict[HostRole, frozenset[str]] = {
    HostRole.BUILDER: frozenset(
        {
            "discover_work",
            "claim_work",
            "report_progress",
            "submit_diff_proposal",
            "submit_builder_claim",
            "escalate",
        }
    ),
    HostRole.VERIFIER: frozenset(
        {
            "discover_work",
            "claim_work",
            "report_progress",
            "submit_verification_result",
            "escalate",
        }
    ),
    HostRole.RELEASE_GUARDIAN: frozenset(
        {
            "approve_release",
            "reject_release",
            "promote_lesson",
            "inspect_audit",
        }
    ),
    HostRole.OPERATOR: frozenset(
        {
            "create_work",
            "assign_work",
            "recover_stale",
            "inspect_audit",
            "approve_release",
            "promote_lesson",
        }
    ),
}


class HostSessionClaims(BaseModel):
    session_id: str = Field(min_length=1, max_length=160)
    client_id: str = Field(min_length=1, max_length=160)
    host_type: str = Field(min_length=1, max_length=80)
    role: HostRole
    actor: str = Field(min_length=1, max_length=160)
    work_item_id: str | None = Field(default=None, min_length=1, max_length=160)
    fence_token: int = Field(default=1, ge=1)
    workspace_root: str | None = Field(default=None, max_length=500)
    issued_at: datetime = Field(default_factory=_utc_now)
    expires_at: datetime

    @field_validator("session_id", "client_id", "host_type", "actor", "work_item_id")
    @classmethod
    def identifiers_must_be_safe(cls, value: str | None) -> str | None:
        if value is None:
            return value
        return _safe_identifier(value)

    @field_validator("workspace_root")
    @classmethod
    def workspace_root_must_not_contain_controls(cls, value: str | None) -> str | None:
        if value is not None and any(separator in value for separator in ["\n", "\r", "\t"]):
            raise ValueError("workspace_root must not contain control separators")
        return value

    @field_validator("issued_at", "expires_at", mode="after")
    @classmethod
    def timestamps_must_be_utc(cls, value: datetime) -> datetime:
        return _normalize_time(value)


class HostSessionToken(BaseModel):
    claims: HostSessionClaims
    signature: str = Field(min_length=1)


class HostSessionManager:
    """Signs, verifies, and enforces role isolation for scoped host sessions."""

    def __init__(self, signing_secret: bytes) -> None:
        if not isinstance(signing_secret, (bytes, bytearray)) or len(signing_secret) < 16:
            raise ValueError("signing_secret must be at least 16 bytes")
        self._signing_secret = bytes(signing_secret)

    @staticmethod
    def _canonical_claims_bytes(claims: HostSessionClaims) -> bytes:
        payload = claims.model_dump(mode="json")
        return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")

    def _sign_claims(self, claims: HostSessionClaims) -> str:
        digest = hmac.new(
            self._signing_secret,
            self._canonical_claims_bytes(claims),
            hashlib.sha256,
        ).digest()
        return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")

    def issue(self, claims: HostSessionClaims) -> HostSessionToken:
        return HostSessionToken(claims=claims, signature=self._sign_claims(claims))

    def verify(
        self,
        token: HostSessionToken,
        *,
        now: datetime | None = None,
        required_role: HostRole | None = None,
        expected_work_item_id: str | None = None,
        min_fence_token: int | None = None,
    ) -> tuple[bool, str | None]:
        expected_sig = self._sign_claims(token.claims)
        if not hmac.compare_digest(expected_sig, token.signature):
            return False, "invalid session token signature"

        current_time = _normalize_time(now or _utc_now())
        if current_time >= _normalize_time(token.claims.expires_at):
            return False, "session token expired"

        if required_role is not None and token.claims.role != required_role:
            return False, f"role mismatch: expected {required_role.value}, got {token.claims.role.value}"

        if expected_work_item_id is not None and token.claims.work_item_id != expected_work_item_id:
            return False, f"work_item_id mismatch: expected {expected_work_item_id}"

        if min_fence_token is not None and token.claims.fence_token < min_fence_token:
            return False, f"stale fence token: {token.claims.fence_token} < {min_fence_token}"

        return True, None

    @staticmethod
    def assert_role_action_allowed(
        claims: HostSessionClaims,
        action: str,
    ) -> tuple[bool, str | None]:
        allowed = _ROLE_ALLOWED_ACTIONS.get(claims.role, frozenset())
        if action in allowed:
            return True, None
        return False, f"action '{action}' is not allowed for role '{claims.role.value}'"
