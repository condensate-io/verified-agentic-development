from vad.deploy.attestation import (
    SignedDeploymentAttestation,
    sign_deployment_attestation,
    verify_deployment_attestation,
)
from vad.signing.local import LocalDevelopmentSigner


def deployment_record():
    return {
        "event": "deployment_apply",
        "provider": "fake",
        "deployment_id": "fake-deployment-1",
        "plan_id": "plan-1",
        "target_id": "staging-api",
        "artifact_digest": "a" * 64,
        "status": "applied",
    }


def test_deployment_attestation_signs_and_verifies():
    signer = LocalDevelopmentSigner("deploy-dev", b"secret")

    attestation = sign_deployment_attestation(deployment_record(), signer)

    assert attestation.signature.key_id == "deploy-dev"
    assert verify_deployment_attestation(attestation, signer) is True


def test_tampered_deployment_attestation_fails_verification():
    signer = LocalDevelopmentSigner("deploy-dev", b"secret")
    attestation = sign_deployment_attestation(deployment_record(), signer)
    payload = attestation.model_dump(mode="json")
    payload["deployment"]["status"] = "tampered"
    tampered = SignedDeploymentAttestation(**payload)

    assert verify_deployment_attestation(tampered, signer) is False
