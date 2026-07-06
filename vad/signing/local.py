import base64
import hashlib
import hmac
from typing import Any

from vad.evidence.bundle import EvidenceBundle
from vad.signing.models import SignatureAlgorithm, SignatureEnvelope


class LocalDevelopmentSigner:
    def __init__(self, key_id: str, secret: bytes):
        if not secret:
            raise ValueError("local signing secret must not be empty")
        self.key_id = key_id
        self._secret = secret

    def sign_payload(self, payload: dict[str, Any]) -> SignatureEnvelope:
        digest = payload_digest(payload)
        signature = hmac.new(self._secret, digest.encode("ascii"), hashlib.sha256).digest()
        return SignatureEnvelope(
            payload_digest=digest,
            key_id=self.key_id,
            algorithm=SignatureAlgorithm.HMAC_SHA256,
            signature=base64.b64encode(signature).decode("ascii"),
        )

    def verify_payload(self, payload: dict[str, Any], envelope: SignatureEnvelope) -> bool:
        if envelope.key_id != self.key_id or envelope.algorithm != SignatureAlgorithm.HMAC_SHA256:
            return False
        expected = self.sign_payload(payload)
        return (
            expected.payload_digest == envelope.payload_digest
            and hmac.compare_digest(expected.signature, envelope.signature)
        )


def payload_digest(payload: dict[str, Any]) -> str:
    return EvidenceBundle(payload).compute_hash()
