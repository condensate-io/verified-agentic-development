from base64 import b64decode
from datetime import datetime, timezone
from enum import Enum
import re

from pydantic import BaseModel, Field, field_validator


class SignatureAlgorithm(str, Enum):
    ED25519 = "ed25519"
    HMAC_SHA256 = "hmac-sha256"


class SignatureEnvelope(BaseModel):
    schema_version: str = "1.0.0"
    payload_digest: str = Field(min_length=64, max_length=64)
    key_id: str = Field(min_length=1, max_length=200)
    algorithm: SignatureAlgorithm
    signature: str = Field(min_length=1)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator("payload_digest")
    @classmethod
    def payload_digest_must_be_sha256_hex(cls, value: str) -> str:
        if not re.fullmatch(r"[0-9a-f]{64}", value):
            raise ValueError("payload_digest must be lowercase SHA-256 hex")
        return value

    @field_validator("key_id")
    @classmethod
    def key_id_must_be_reference(cls, value: str) -> str:
        if any(char.isspace() for char in value):
            raise ValueError("key_id must be a compact key reference")
        return value

    @field_validator("signature")
    @classmethod
    def signature_must_be_base64(cls, value: str) -> str:
        try:
            decoded = b64decode(value, validate=True)
        except Exception as exc:
            raise ValueError("signature must be base64") from exc
        if not decoded:
            raise ValueError("signature must decode to bytes")
        return value
