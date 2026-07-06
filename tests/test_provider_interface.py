import pytest
from pydantic import ValidationError

from vad.router.providers.interface import ProviderInventory, ProviderModel, require_provider_inventory


class ContractProvider:
    def inventory(self):
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


def test_provider_exposes_inventory_capabilities_cost_and_context_limits():
    inventory = require_provider_inventory(ContractProvider())

    assert inventory.provider_name == "fake"
    assert inventory.capabilities == ["chat", "tool-use"]
    assert inventory.models[0].cost_per_1k_input_tokens == 0.01
    assert inventory.models[0].context_limit == 4096


@pytest.mark.parametrize(
    "model_payload",
    [
        {"name": "", "tier": "tier1", "capabilities": ["chat"], "cost_per_1k_input_tokens": 0.01, "cost_per_1k_output_tokens": 0.02, "context_limit": 4096},
        {"name": "fake", "tier": "tier1", "capabilities": [], "cost_per_1k_input_tokens": 0.01, "cost_per_1k_output_tokens": 0.02, "context_limit": 4096},
        {"name": "fake", "tier": "tier1", "capabilities": ["chat"], "cost_per_1k_input_tokens": -1, "cost_per_1k_output_tokens": 0.02, "context_limit": 4096},
        {"name": "fake", "tier": "tier1", "capabilities": ["chat"], "cost_per_1k_input_tokens": 0.01, "cost_per_1k_output_tokens": 0.02, "context_limit": 0},
    ],
)
def test_missing_model_metadata_blocks_routing(model_payload):
    with pytest.raises(ValidationError):
        ProviderModel(**model_payload)


def test_unsupported_model_capability_blocks_routing():
    with pytest.raises(ValidationError):
        ProviderInventory(
            provider_name="fake",
            provider_version="1.0",
            capabilities=["chat"],
            models=[
                ProviderModel(
                    name="fake-tools",
                    tier="tier1",
                    capabilities=["tool-use"],
                    cost_per_1k_input_tokens=0.01,
                    cost_per_1k_output_tokens=0.02,
                    context_limit=4096,
                )
            ],
        )
