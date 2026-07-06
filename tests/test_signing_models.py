from datetime import datetime

import pytest
from pydantic import ValidationError

from vad.signing.models import SignatureAlgorithm, SignatureEnvelope


def test_signature_envelope_records_required_fields():
    envelope = SignatureEnvelope(
        payload_digest="a" * 64,
        key_id="local-dev",
        algorithm=SignatureAlgorithm.HMAC_SHA256,
        signature="YWJj",
    )

    assert envelope.payload_digest == "a" * 64
    assert envelope.key_id == "local-dev"
    assert envelope.algorithm == SignatureAlgorithm.HMAC_SHA256
    assert isinstance(envelope.created_at, datetime)


@pytest.mark.parametrize(
    "payload",
    [
        {"payload_digest": "A" * 64, "key_id": "local-dev", "algorithm": "hmac-sha256", "signature": "YWJj"},
        {"payload_digest": "a" * 63, "key_id": "local-dev", "algorithm": "hmac-sha256", "signature": "YWJj"},
        {"payload_digest": "a" * 64, "key_id": "local dev", "algorithm": "hmac-sha256", "signature": "YWJj"},
        {"payload_digest": "a" * 64, "key_id": "local-dev", "algorithm": "unknown", "signature": "YWJj"},
        {"payload_digest": "a" * 64, "key_id": "local-dev", "algorithm": "hmac-sha256", "signature": "not base64"},
    ],
)
def test_malformed_signature_envelope_fails_validation(payload):
    with pytest.raises(ValidationError):
        SignatureEnvelope(**payload)
