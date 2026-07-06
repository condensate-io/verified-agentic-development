import os
from typing import Any

from vad.router.providers.fake import ProviderCompletionRequest, ProviderCompletionResult
from vad.router.providers.interface import ProviderInventory, ProviderModel


class OpenAIProvider:
    def __init__(self, client: Any | None = None, model: str = "gpt-4.1-mini"):
        self.client = client
        self.model = model

    def inventory(self) -> ProviderInventory:
        return ProviderInventory(
            provider_name="openai",
            provider_version="responses-v1",
            capabilities=["chat", "tool-use"],
            models=[
                ProviderModel(
                    name=self.model,
                    tier="tier2",
                    capabilities=["chat", "tool-use"],
                    cost_per_1k_input_tokens=0,
                    cost_per_1k_output_tokens=0,
                    context_limit=128000,
                )
            ],
        )

    def complete(self, request: ProviderCompletionRequest) -> ProviderCompletionResult:
        client = self.client or self._load_client()
        response = client.responses.create(
            model=request.model,
            input=request.prompt,
            max_output_tokens=request.max_output_tokens,
        )
        output_text = getattr(response, "output_text", "")
        usage = getattr(response, "usage", None)
        input_tokens = getattr(usage, "input_tokens", 0) if usage else 0
        output_tokens = getattr(usage, "output_tokens", 0) if usage else 0
        request_id = getattr(response, "id", None)
        evidence = {
            "event": "provider_completion",
            "provider": "openai",
            "provider_request_id": request_id,
            "model": request.model,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "estimated_cost": 0,
        }
        return ProviderCompletionResult(
            allowed=True,
            provider_request_id=request_id,
            model=request.model,
            output_text=output_text,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            estimated_cost=0,
            evidence=evidence,
        )

    @staticmethod
    def live_enabled() -> bool:
        return os.getenv("VAD_LIVE_OPENAI_PROVIDER_TEST") == "1"

    @staticmethod
    def _load_client():
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise RuntimeError("OpenAI SDK is not installed; install the 'openai' optional extra") from exc
        return OpenAI()
