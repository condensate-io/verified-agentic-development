from vad.signing.local import LocalDevelopmentSigner
from vad.signing.models import SignatureEnvelope


def test_local_development_signer_signs_and_verifies_evidence():
    payload = {"run_id": "run-1", "final_decision": "passed"}
    signer = LocalDevelopmentSigner("local-dev", b"secret")

    envelope = signer.sign_payload(payload)

    assert isinstance(envelope, SignatureEnvelope)
    assert signer.verify_payload(payload, envelope) is True


def test_local_development_signer_rejects_tampered_payload():
    signer = LocalDevelopmentSigner("local-dev", b"secret")
    envelope = signer.sign_payload({"run_id": "run-1", "final_decision": "passed"})

    assert signer.verify_payload({"run_id": "run-1", "final_decision": "failed"}, envelope) is False


def test_local_development_signer_private_key_is_not_serialized():
    signer = LocalDevelopmentSigner("local-dev", b"secret")
    envelope = signer.sign_payload({"run_id": "run-1"})

    assert "secret" not in envelope.model_dump_json()
    assert "_secret" not in envelope.model_dump()
