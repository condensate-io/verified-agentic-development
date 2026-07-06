from typing import Any

from pydantic import BaseModel

from vad.signing.local import LocalDevelopmentSigner
from vad.signing.models import SignatureEnvelope


class SignedDeploymentAttestation(BaseModel):
    deployment: dict[str, Any]
    signature: SignatureEnvelope


def sign_deployment_attestation(
    deployment: dict[str, Any],
    signer: LocalDevelopmentSigner,
) -> SignedDeploymentAttestation:
    return SignedDeploymentAttestation(
        deployment=deployment,
        signature=signer.sign_payload(deployment),
    )


def verify_deployment_attestation(
    attestation: SignedDeploymentAttestation,
    signer: LocalDevelopmentSigner,
) -> bool:
    return signer.verify_payload(attestation.deployment, attestation.signature)
