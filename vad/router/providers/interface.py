from typing import Protocol

from pydantic import BaseModel, Field, model_validator


class ProviderModel(BaseModel):
    name: str = Field(min_length=1)
    tier: str = Field(min_length=1)
    capabilities: list[str] = Field(min_length=1)
    cost_per_1k_input_tokens: float = Field(ge=0)
    cost_per_1k_output_tokens: float = Field(ge=0)
    context_limit: int = Field(gt=0)


class ProviderInventory(BaseModel):
    provider_name: str = Field(min_length=1)
    provider_version: str = Field(min_length=1)
    capabilities: list[str] = Field(min_length=1)
    models: list[ProviderModel] = Field(min_length=1)

    @model_validator(mode="after")
    def models_must_match_provider_capabilities(self):
        supported = set(self.capabilities)
        for model in self.models:
            missing = set(model.capabilities) - supported
            if missing:
                raise ValueError(f"model {model.name} declares unsupported capabilities: {', '.join(sorted(missing))}")
        return self


class ModelProvider(Protocol):
    def inventory(self) -> ProviderInventory:
        ...


def require_provider_inventory(provider: ModelProvider) -> ProviderInventory:
    inventory = provider.inventory()
    if not inventory.models:
        raise ValueError("provider inventory has no models")
    return inventory
