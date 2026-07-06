from vad.evidence.recorder import EvidenceRecorder
from vad.router.drift import detect_provider_drift
from vad.router.privacy import redact_provider_payload, redaction_digest
from vad.router.providers.interface import ProviderInventory, ProviderModel


def inventory(context_limit=4096, tier="tier1", capabilities=None):
    return ProviderInventory(
        provider_name="fake",
        provider_version="1.0",
        capabilities=["chat", "tool-use"],
        models=[
            ProviderModel(
                name="fake-chat",
                tier=tier,
                capabilities=capabilities or ["chat"],
                cost_per_1k_input_tokens=0.01,
                cost_per_1k_output_tokens=0.02,
                context_limit=context_limit,
            )
        ],
    )


def test_sensitive_prompt_fields_are_redacted_in_evidence():
    request = {"model": "fake-chat", "prompt": "secret project text", "metadata": {"token": "abc"}}

    evidence = EvidenceRecorder().create_provider_call_evidence("fake", request)

    assert evidence["request"]["prompt"] == "[redacted]"
    assert evidence["request"]["metadata"]["token"] == "[redacted]"
    assert "secret project text" not in str(evidence)
    assert evidence["request_redaction_digest"] == redaction_digest(request)


def test_redaction_preserves_non_sensitive_fields():
    redacted = redact_provider_payload({"model": "fake-chat", "temperature": 0})

    assert redacted == {"model": "fake-chat", "temperature": 0}


def test_provider_metadata_drift_warns_for_tier_change():
    decision = detect_provider_drift(inventory(), inventory(tier="tier2"))

    assert decision["allow"] is True
    assert decision["warnings"] == ["tier changed for fake-chat"]
    assert decision["denials"] == []


def test_provider_metadata_drift_blocks_context_regression():
    decision = detect_provider_drift(inventory(context_limit=4096), inventory(context_limit=1024))

    assert decision["allow"] is False
    assert decision["denials"] == ["context limit decreased for fake-chat"]
