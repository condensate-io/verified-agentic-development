from vad.router.providers.fake import FakeProvider, ProviderCompletionRequest


def test_fake_provider_successful_completion_records_request_id():
    provider = FakeProvider()

    result = provider.complete(ProviderCompletionRequest(prompt="hello model", model="fake-chat"))

    assert result.allowed is True
    assert result.provider_request_id == "fake-1"
    assert result.evidence["provider_request_id"] == "fake-1"
    assert result.output_text


def test_fake_provider_timeout_records_retry_denial_event():
    provider = FakeProvider(delay_seconds=10)

    result = provider.complete(ProviderCompletionRequest(prompt="hello model", model="fake-chat", timeout_seconds=1))

    assert result.allowed is False
    assert result.denial == "provider timeout"
    assert result.evidence["event"] == "provider_denied"
    assert result.evidence["retryable"] is True


def test_fake_provider_records_token_usage_and_cost():
    provider = FakeProvider()

    result = provider.complete(ProviderCompletionRequest(prompt="one two three four", model="fake-chat"))

    assert result.input_tokens == 4
    assert result.output_tokens > 0
    assert result.estimated_cost > 0
    assert result.evidence["input_tokens"] == result.input_tokens
    assert result.evidence["estimated_cost"] == result.estimated_cost
