"""DSSE (Dead Simple Signing Envelope) implementation according to the in-toto specification."""

import base64
import hashlib
import hmac
import json
from typing import Any, Dict, List, Optional, Tuple
from pydantic import BaseModel, Field


class DSSESignature(BaseModel):
    keyid: str
    sig: str


class DSSEEnvelope(BaseModel):
    payloadType: str
    payload: str  # Base64-encoded payload
    signatures: List[DSSESignature] = Field(default_factory=list)


class DSSEAttestor:
    """Signs and verifies in-toto DSSE envelopes for rebase-stable review attestations."""

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
        key_id: str = "default-key",
        secret_key: bytes = b"vad-enterprise-signing-secret",
    ) -> DSSEEnvelope:
        pae = self.pre_authentication_encoding(payload_type, payload_bytes)
        signature_bytes = hmac.new(secret_key, pae, hashlib.sha256).digest()
        sig_b64 = base64.b64encode(signature_bytes).decode("utf-8")
        payload_b64 = base64.b64encode(payload_bytes).decode("utf-8")

        return DSSEEnvelope(
            payloadType=payload_type,
            payload=payload_b64,
            signatures=[DSSESignature(keyid=key_id, sig=sig_b64)],
        )

    def verify(
        self, envelope: DSSEEnvelope, secret_key: bytes = b"vad-enterprise-signing-secret"
    ) -> Tuple[bool, Optional[bytes], Optional[str]]:
        """Verifies signature on envelope. Returns (is_valid, decoded_payload_bytes, error_message)."""
        try:
            payload_bytes = base64.b64decode(envelope.payload)
        except Exception:
            return False, None, "Invalid base64 payload"

        if not envelope.signatures:
            return False, None, "Envelope has no signatures"

        pae = self.pre_authentication_encoding(envelope.payloadType, payload_bytes)
        expected_sig_bytes = hmac.new(secret_key, pae, hashlib.sha256).digest()

        for sig_entry in envelope.signatures:
            try:
                actual_sig_bytes = base64.b64decode(sig_entry.sig)
            except Exception:
                continue

            if hmac.compare_digest(expected_sig_bytes, actual_sig_bytes):
                return True, payload_bytes, None

        return False, None, "Signature mismatch"

    def create_review_attestation(
        self,
        commit_sha: str,
        review_decision: str,
        reviewer_agent: str,
        proof_summary: Dict[str, Any],
        secret_key: bytes = b"vad-enterprise-signing-secret",
    ) -> DSSEEnvelope:
        statement = {
            "_type": "https://in-toto.io/Statement/v1",
            "subject": [{"name": "git-commit", "digest": {"sha1": commit_sha}}],
            "predicateType": "https://vad.dev/attestation/review/v1",
            "predicate": {
                "decision": review_decision,
                "reviewer": reviewer_agent,
                "proof_summary": proof_summary,
            },
        }
        payload_bytes = json.dumps(statement, sort_keys=True).encode("utf-8")
        return self.sign(payload_bytes, payload_type="application/vnd.in-toto+json", secret_key=secret_key)

    def verify_review_attestation(
        self,
        envelope: DSSEEnvelope,
        expected_commit_sha: str,
        secret_key: bytes = b"vad-enterprise-signing-secret",
    ) -> bool:
        valid, payload_bytes, err = self.verify(envelope, secret_key=secret_key)
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
