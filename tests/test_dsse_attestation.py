import base64
import pytest
from vad.review.dsse_attestation import DSSEAttestor, DSSEEnvelope


def test_dsse_signing_and_verification():
    attestor = DSSEAttestor()
    secret = b"my-test-signing-key-12345"
    payload = b'{"msg": "proofs verified", "score": 98}'

    envelope = attestor.sign(payload, key_id="key-1", secret_key=secret)
    assert envelope.payloadType == "application/vnd.vad.review+json"
    assert len(envelope.signatures) == 1

    # Successful verification
    is_valid, decoded, err = attestor.verify(envelope, secret_key=secret)
    assert is_valid is True
    assert decoded == payload
    assert err is None

    # Tampered payload fails verification
    tampered_envelope = DSSEEnvelope(
        payloadType=envelope.payloadType,
        payload=base64.b64encode(b'{"msg": "tampered"}').decode("utf-8"),
        signatures=envelope.signatures,
    )
    is_valid, decoded, err = attestor.verify(tampered_envelope, secret_key=secret)
    assert is_valid is False
    assert err == "Signature mismatch"


def test_dsse_review_attestation():
    attestor = DSSEAttestor()
    secret = b"test-enterprise-secret"
    commit_sha = "a1b2c3d4e5f678901234567890abcdef12345678"

    envelope = attestor.create_review_attestation(
        commit_sha=commit_sha,
        review_decision="approved",
        reviewer_agent="adversarial-verifier",
        proof_summary={"unit": "passed", "security": "passed"},
        secret_key=secret,
    )

    # Valid commit verification
    assert attestor.verify_review_attestation(envelope, commit_sha, secret_key=secret) is True

    # Different commit fails
    assert attestor.verify_review_attestation(envelope, "0000000000000000000000000000000000000000", secret_key=secret) is False
