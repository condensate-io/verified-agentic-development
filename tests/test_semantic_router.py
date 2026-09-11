import pytest
from unittest.mock import patch
import httpx
from vad.decision.lexical_router import SkillDescriptor
from vad.decision.semantic_router import SemanticRouter


@pytest.fixture
def sample_descriptors():
    return [
        SkillDescriptor(
            id="skill-billing",
            name="Payment and Invoicing Service",
            description="Processes credit cards, invoices, receipts, subscriptions, and chargebacks.",
            keywords=["stripe", "payment", "card", "invoice"],
            domain="finance",
            target_agent="billing-specialist",
        ),
        SkillDescriptor(
            id="skill-audio",
            name="Telephony Audio Streaming",
            description="Low-latency RTP streaming, codec conversion, jitter buffers, and packet loss.",
            keywords=["rtp", "sip", "audio", "codec", "stream"],
            domain="voice",
            target_agent="audio-pipeline-engineer",
        ),
    ]


def test_semantic_router_local_fallback(sample_descriptors):
    router = SemanticRouter(service_url=None, fallback_local=True)
    router.index(sample_descriptors)

    result = router.route("Handle customer credit card chargebacks and subscription renewal")
    assert result.match_id == "skill-billing"
    assert result.target_agent == "billing-specialist"
    assert result.is_confident is True
    assert result.provider == "local-vector"

    result_voice = router.route("Debug jitter and packet loss on audio RTP stream")
    assert result_voice.match_id == "skill-audio"
    assert result_voice.target_agent == "audio-pipeline-engineer"
    assert result_voice.is_confident is True


def test_semantic_router_remote_service_success(sample_descriptors):
    router = SemanticRouter(service_url="http://127.0.0.1:8099/classify")
    router.index(sample_descriptors)

    mock_response = httpx.Response(
        status_code=200,
        json={"match_id": "skill-billing", "similarity": 0.88, "target_agent": "billing-specialist"},
        request=httpx.Request("POST", "http://127.0.0.1:8099/classify"),
    )

    with patch("httpx.Client.post", return_value=mock_response):
        result = router.route("Need help with Stripe invoice webhook")
        assert result.match_id == "skill-billing"
        assert result.provider == "remote-service"
        assert result.similarity == 0.88
        assert result.is_confident is True


def test_semantic_router_remote_failure_gracefully_falls_back(sample_descriptors):
    router = SemanticRouter(service_url="http://127.0.0.1:8099/classify", fallback_local=True)
    router.index(sample_descriptors)

    # When remote raises connection error, it should transparently fall back to local vector matching
    with patch("httpx.Client.post", side_effect=httpx.ConnectError("Connection refused")):
        result = router.route("Process credit cards and invoices")
        assert result.match_id == "skill-billing"
        assert result.provider == "local-vector"
        assert result.is_confident is True
