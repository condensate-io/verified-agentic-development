from pathlib import Path


DOCS = Path(__file__).resolve().parents[1] / "docs" / "providers.md"


def test_upstream_provider_matrix_documents_status_and_live_gates():
    text = DOCS.read_text(encoding="utf-8")

    assert "## Upstream Provider Matrix" in text
    assert "| OpenAI | implemented optional adapter |" in text
    assert "`VAD_LIVE_OPENAI_PROVIDER_TEST=1`" in text
    assert "Default CI must never call live providers" in text


def test_future_upstream_providers_are_not_claimed_as_implemented():
    text = DOCS.read_text(encoding="utf-8")

    for provider in ["Anthropic", "Google Gemini", "Azure OpenAI", "AWS Bedrock"]:
        assert f"| {provider} | planned |" in text


def test_provider_docs_require_deterministic_tests_for_new_sdks():
    text = DOCS.read_text(encoding="utf-8")

    assert "New SDK packages require explicit approval" in text
    assert "deterministic injected-client tests" in text
