"""DSSE (Dead Simple Signing Envelope) implementation according to the in-toto specification."""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any, List, Optional, Tuple

from pydantic import BaseModel, Field

from vad.signing.ed25519_local import (
    DSSEKeyProvider,
    ExplicitHMACKeyProvider,
    LocalEd25519KeyProvider,
)

LEGACY_HMAC_SECRET = b"vad-enterprise-signing-secret"


class DSSESignature(BaseModel):
    keyid: str
    sig: str


class DSSEEnvelope(BaseModel):
    payloadType: str
    payload: str  # Base64-encoded payload
    signatures: List[DSSESignature] = Field(default_factory=list)


def decode_attestation_statement(envelope: DSSEEnvelope) -> dict[str, Any]:
    """Decode and parse the JSON statement payload from a DSSE envelope."""
    try:
        payload_bytes = base64.b64decode(envelope.payload, validate=True)
    except Exception as exc:
        raise ValueError("Invalid base64 DSSE payload") from exc

    try:
        statement = json.loads(payload_bytes.decode("utf-8"))
    except Exception as exc:
        raise ValueError("Invalid UTF-8 JSON DSSE payload") from exc

    if not isinstance(statement, dict):
        raise ValueError("Decoded DSSE statement must be a JSON object")
    return statement


class DSSEAttestor:
    """Signs and verifies in-toto DSSE envelopes for rebase-stable review attestations."""

    def __init__(
        self,
        key_provider: DSSEKeyProvider | None = None,
        key_dir: str | Path | None = None,
        strict: bool = False,
    ):
        self._key_dir = Path(key_dir) if key_dir is not None else None
        self._key_provider: DSSEKeyProvider | None = key_provider
        if self._key_provider is None and self._key_dir is not None:
            self._key_provider = LocalEd25519KeyProvider(key_dir=self._key_dir)
        self.strict = strict

    @property
    def key_provider(self) -> DSSEKeyProvider:
        if self._key_provider is None:
            self._key_provider = LocalEd25519KeyProvider(key_dir=self._key_dir)
        return self._key_provider

    def _resolve_provider(
        self,
        *,
        secret_key: bytes | None = None,
        key_id: str | None = None,
    ) -> DSSEKeyProvider:
        if secret_key is not None:
            if self.strict and secret_key == LEGACY_HMAC_SECRET:
                raise ValueError(
                    "Legacy hardcoded HMAC secret is forbidden in strict mode; "
                    "use an Ed25519 key provider or an explicit non-legacy secret"
                )
            return ExplicitHMACKeyProvider(
                key_id=key_id or "default-key",
                secret_key=secret_key,
            )
        return self.key_provider

    @staticmethod
    def pre_authentication_encoding(payload_type: str, payload_bytes: bytes) -> bytes:
        """DSSE Pre-Authentication Encoding (PAE):

        PAE(type, body) = "DSSEv1" + " " + len(type) + " " + type + " " + len(body) + " " + body
        """
        type_bytes = payload_type.encode("utf-8")
        pae = (
            b"DSSEv1 "
            + str(len(type_bytes)).encode("utf-8")
            + b" "
            + type_bytes
            + b" "
            + str(len(payload_bytes)).encode("utf-8")
            + b" "
            + payload_bytes
        )
        return pae

    def sign(
        self,
        payload_bytes: bytes,
        payload_type: str = "application/vnd.vad.review+json",
        key_id: str | None = None,
        secret_key: bytes | None = None,
    ) -> DSSEEnvelope:
        provider = self._resolve_provider(secret_key=secret_key, key_id=key_id)
        pae = self.pre_authentication_encoding(payload_type, payload_bytes)
        signature_bytes = provider.sign_pae(pae)
        sig_b64 = base64.b64encode(signature_bytes).decode("utf-8")
        payload_b64 = base64.b64encode(payload_bytes).decode("utf-8")
        resolved_key_id = key_id if (secret_key is None and key_id is not None) else provider.key_id

        return DSSEEnvelope(
            payloadType=payload_type,
            payload=payload_b64,
            signatures=[DSSESignature(keyid=resolved_key_id, sig=sig_b64)],
        )

    def verify(
        self,
        envelope: DSSEEnvelope,
        secret_key: bytes | None = None,
    ) -> Tuple[bool, Optional[bytes], Optional[str]]:
        """Verifies signature on envelope. Returns (is_valid, decoded_payload_bytes, error_message)."""
        provider = self._resolve_provider(secret_key=secret_key)

        try:
            payload_bytes = base64.b64decode(envelope.payload, validate=True)
        except Exception:
            return False, None, "Invalid base64 payload"

        if not envelope.signatures:
            return False, None, "Envelope has no signatures"

        pae = self.pre_authentication_encoding(envelope.payloadType, payload_bytes)

        for sig_entry in envelope.signatures:
            try:
                actual_sig_bytes = base64.b64decode(sig_entry.sig, validate=True)
            except Exception:
                continue

            if provider.verify_pae(pae, actual_sig_bytes):
                return True, payload_bytes, None

        return False, None, "Signature mismatch"

    def create_review_attestation(
        self,
        commit_sha: str,
        review_decision: str,
        reviewer_agent: str,
        proof_summary: dict[str, Any],
        secret_key: bytes | None = None,
        eip_digest: str | None = None,
        proof_plan_digest: str | None = None,
        assurance_level: str | None = None,
        verified_at: str | None = None,
        expires_at: str | None = None,
    ) -> DSSEEnvelope:
        predicate: dict[str, Any] = {
            "decision": review_decision,
            "reviewer": reviewer_agent,
            "proof_summary": proof_summary,
        }
        if eip_digest is not None:
            predicate["eip_digest"] = eip_digest
        if proof_plan_digest is not None:
            predicate["proof_plan_digest"] = proof_plan_digest
        if assurance_level is not None:
            predicate["assurance_level"] = assurance_level
        if verified_at is not None:
            predicate["verified_at"] = verified_at
        if expires_at is not None:
            predicate["expires_at"] = expires_at

        statement = {
            "_type": "https://in-toto.io/Statement/v1",
            "subject": [{"name": "git-commit", "digest": {"sha1": commit_sha}}],
            "predicateType": "https://vad.dev/attestation/review/v1",
            "predicate": predicate,
        }
        payload_bytes = json.dumps(statement, sort_keys=True).encode("utf-8")
        return self.sign(
            payload_bytes,
            payload_type="application/vnd.in-toto+json",
            secret_key=secret_key,
        )

    def verify_review_attestation(
        self,
        envelope: DSSEEnvelope,
        expected_commit_sha: str,
        secret_key: bytes | None = None,
    ) -> bool:
        valid, payload_bytes, _ = self.verify(envelope, secret_key=secret_key)
        if not valid or not payload_bytes:
            return False

        try:
            stmt = json.loads(payload_bytes.decode("utf-8"))
            subjects = stmt.get("subject", [])
            for subj in subjects:
                digest = subj.get("digest", {})
                if digest.get("sha1") == expected_commit_sha:
                    return stmt.get("predicate", {}).get("decision") == "approved"
        except Exception:
            return False
        return False
