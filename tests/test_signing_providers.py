import time

from vad.signing.local import LocalDevelopmentSigner
from vad.signing.providers import ExternalSigner


class FakeExternalProvider:
    def __init__(self):
        self.signer = LocalDevelopmentSigner("external-dev", b"secret")
        self.requests = []

    def sign(self, payload, key_id):
        self.requests.append({"operation": "sign", "payload": payload, "key_id": key_id})
        return self.signer.sign_payload(payload)

    def verify(self, payload, envelope):
        self.requests.append({"operation": "verify", "payload": payload, "key_id": envelope.key_id})
        return self.signer.verify_payload(payload, envelope)


class SlowExternalProvider(FakeExternalProvider):
    def sign(self, payload, key_id):
        time.sleep(0.05)
        return super().sign(payload, key_id)


def test_fake_external_signer_verifies_request_contract():
    provider = FakeExternalProvider()
    signer = ExternalSigner(provider, allowed_key_ids={"external-dev"})
    payload = {"run_id": "run-1"}

    signed = signer.sign(payload, "external-dev")
    verified = signer.verify(payload, signed.envelope)

    assert signed.allowed is True
    assert verified.allowed is True
    assert verified.verified is True
    assert provider.requests[0] == {"operation": "sign", "payload": payload, "key_id": "external-dev"}
    assert provider.requests[1] == {"operation": "verify", "payload": payload, "key_id": "external-dev"}


def test_external_signer_timeout_fails_closed():
    signer = ExternalSigner(SlowExternalProvider(), allowed_key_ids={"external-dev"}, timeout_seconds=0.001)

    result = signer.sign({"run_id": "run-1"}, "external-dev")

    assert result.allowed is False
    assert result.envelope is None
    assert result.denial == "external signer timed out"


def test_external_signer_unknown_key_id_fails_closed():
    signer = ExternalSigner(FakeExternalProvider(), allowed_key_ids={"external-dev"})

    result = signer.sign({"run_id": "run-1"}, "other-key")

    assert result.allowed is False
    assert result.denial == "unknown key id: other-key"
