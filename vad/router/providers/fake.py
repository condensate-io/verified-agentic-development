from itertools import count

from pydantic import BaseModel, Field

from vad.router.providers.interface import ProviderInventory, ProviderModel


class ProviderCompletionRequest(BaseModel):
    prompt: str = Field(min_length=1)
    model: str
    max_output_tokens: int = Field(default=256, ge=1)
    timeout_seconds: float = Field(default=5.0, gt=0)


class ProviderCompletionResult(BaseModel):
    allowed: bool
    provider_request_id: str | None = None
    model: str | None = None
    output_text: str = ""
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    estimated_cost: float = Field(default=0, ge=0)
    evidence: dict
    denial: str | None = None


class FakeProvider:
    def __init__(self, delay_seconds: float = 0):
        self.delay_seconds = delay_seconds
        self._ids = count(1)

    def inventory(self) -> ProviderInventory:
        return ProviderInventory(
            provider_name="fake",
            provider_version="1.0",
            capabilities=["chat", "tool-use"],
            models=[
                ProviderModel(
                    name="fake-chat",
                    tier="tier1",
                    capabilities=["chat"],
                    cost_per_1k_input_tokens=0.01,
                    cost_per_1k_output_tokens=0.02,
                    context_limit=4096,
                )
            ],
        )

    def complete(self, request: ProviderCompletionRequest) -> ProviderCompletionResult:
        model = self._model(request.model)
        request_id = f"fake-{next(self._ids)}"
        if model is None:
            return _denied_result(request_id, "unknown model", retryable=False)
        if self.delay_seconds > request.timeout_seconds:
            return _denied_result(request_id, "provider timeout", retryable=True)

        input_tokens = _estimate_tokens(request.prompt)
        output_text = f"fake completion for {request.prompt[:40]}"
        output_tokens = min(request.max_output_tokens, _estimate_tokens(output_text))
        estimated_cost = (
            (input_tokens / 1000) * model.cost_per_1k_input_tokens
            + (output_tokens / 1000) * model.cost_per_1k_output_tokens
        )
        evidence = {
            "event": "provider_completion",
            "provider": "fake",
            "provider_request_id": request_id,
            "model": model.name,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "estimated_cost": estimated_cost,
        }
        return ProviderCompletionResult(
            allowed=True,
            provider_request_id=request_id,
            model=model.name,
            output_text=output_text,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            estimated_cost=estimated_cost,
            evidence=evidence,
        )

    def _model(self, name: str) -> ProviderModel | None:
        return next((model for model in self.inventory().models if model.name == name), None)


def _denied_result(request_id: str, denial: str, retryable: bool) -> ProviderCompletionResult:
    return ProviderCompletionResult(
        allowed=False,
        provider_request_id=request_id,
        denial=denial,
        evidence={
            "event": "provider_denied",
            "provider": "fake",
            "provider_request_id": request_id,
            "denial": denial,
            "retryable": retryable,
        },
    )


def _estimate_tokens(text: str) -> int:
    return max(1, len(text.split()))
