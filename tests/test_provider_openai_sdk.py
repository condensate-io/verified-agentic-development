import os

import pytest

from vad.router.providers.fake import ProviderCompletionRequest
from vad.router.providers.openai_sdk import OpenAIProvider


class FakeUsage:
    input_tokens = 3
    output_tokens = 5


class FakeResponse:
    id = "resp_123"
    output_text = "hello"
    usage = FakeUsage()


class FakeResponses:
    def __init__(self):
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return FakeResponse()


class FakeClient:
    def __init__(self):
        self.responses = FakeResponses()


def test_openai_adapter_imports_without_openai_sdk_installed():
    provider = OpenAIProvider()

    inventory = provider.inventory()

    assert inventory.provider_name == "openai"
    assert inventory.models[0].name == "gpt-4.1-mini"


def test_openai_adapter_uses_injected_client_without_network_or_credentials():
    client = FakeClient()
    provider = OpenAIProvider(client=client)

    result = provider.complete(ProviderCompletionRequest(prompt="hello", model="gpt-4.1-mini"))

    assert result.provider_request_id == "resp_123"
    assert result.output_text == "hello"
    assert result.input_tokens == 3
    assert client.responses.calls[0]["input"] == "hello"


@pytest.mark.skipif(os.getenv("VAD_LIVE_OPENAI_PROVIDER_TEST") != "1", reason="live OpenAI provider smoke test is opt-in")
def test_openai_live_smoke_is_opt_in():
    provider = OpenAIProvider()

    result = provider.complete(ProviderCompletionRequest(prompt="Say ok.", model="gpt-4.1-mini", max_output_tokens=16))

    assert result.provider_request_id
