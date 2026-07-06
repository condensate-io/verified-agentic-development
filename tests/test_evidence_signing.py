from vad.evidence.bundle import EvidenceBundle
from vad.evidence.recorder import EvidenceRecorder
from vad.signing.local import LocalDevelopmentSigner


def test_signed_run_evidence_preserves_original_deterministic_hash():
    payload = {"run_id": "run-1", "final_decision": "passed"}
    original_hash = EvidenceBundle(payload).compute_hash()
    envelope = LocalDevelopmentSigner("local-dev", b"secret").sign_payload(payload)

    signed = EvidenceRecorder().create_signed_evidence(payload, envelope)

    assert signed.payload_hash == original_hash
    assert signed.signature.payload_digest == original_hash


def test_signature_verification_result_is_evidence_producing():
    payload = {"run_id": "run-1"}
    signer = LocalDevelopmentSigner("local-dev", b"secret")
    envelope = signer.sign_payload(payload)
    verified = signer.verify_payload(payload, envelope)

    evidence = EvidenceRecorder().create_signature_verification_evidence(envelope, verified)

    assert evidence.key_id == "local-dev"
    assert evidence.verified is True
    assert evidence.payload_digest == envelope.payload_digest
