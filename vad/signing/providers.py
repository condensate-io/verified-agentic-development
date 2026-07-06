from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from typing import Any, Protocol

from pydantic import BaseModel, Field

from vad.signing.models import SignatureEnvelope


class ExternalSignerResult(BaseModel):
    allowed: bool
    envelope: SignatureEnvelope | None = None
    verified: bool | None = None
    denial: str | None = None


class ExternalSignerProvider(Protocol):
    def sign(self, payload: dict[str, Any], key_id: str) -> SignatureEnvelope:
        ...

    def verify(self, payload: dict[str, Any], envelope: SignatureEnvelope) -> bool:
        ...


class ExternalSigner:
    def __init__(
        self,
        provider: ExternalSignerProvider,
        allowed_key_ids: set[str],
        timeout_seconds: float = 5.0,
    ):
        self.provider = provider
        self.allowed_key_ids = allowed_key_ids
        self.timeout_seconds = timeout_seconds

    def sign(self, payload: dict[str, Any], key_id: str) -> ExternalSignerResult:
        if key_id not in self.allowed_key_ids:
            return ExternalSignerResult(allowed=False, denial=f"unknown key id: {key_id}")
        try:
            envelope = self._run_with_timeout(lambda: self.provider.sign(payload, key_id))
        except TimeoutError:
            return ExternalSignerResult(allowed=False, denial="external signer timed out")
        return ExternalSignerResult(allowed=True, envelope=envelope)

    def verify(self, payload: dict[str, Any], envelope: SignatureEnvelope) -> ExternalSignerResult:
        if envelope.key_id not in self.allowed_key_ids:
            return ExternalSignerResult(allowed=False, verified=False, denial=f"unknown key id: {envelope.key_id}")
        try:
            verified = self._run_with_timeout(lambda: self.provider.verify(payload, envelope))
        except TimeoutError:
            return ExternalSignerResult(allowed=False, verified=False, denial="external signer timed out")
        return ExternalSignerResult(allowed=bool(verified), verified=bool(verified), denial=None if verified else "signature verification failed")

    def _run_with_timeout(self, call):
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(call)
            try:
                return future.result(timeout=self.timeout_seconds)
            except FutureTimeout as exc:
                future.cancel()
                raise TimeoutError("external signer timed out") from exc
