import pytest
from pydantic import ValidationError

from vad.adapters.a2a import A2AAdapter, A2AMessage


def test_a2a_message_serializes():
    message = A2AMessage(
        sender="builder",
        receiver="verifier",
        action="verify",
        payload={"proof_plan": "proof.yaml"},
    )

    payload = message.model_dump(mode="json")

    assert payload["message_id"]
    assert payload["sender"] == "builder"
    assert payload["receiver"] == "verifier"
    assert payload["action"] == "verify"
    assert payload["created_at"]
    assert A2AMessage(**payload) == message


def test_a2a_message_requires_sender_and_receiver():
    with pytest.raises(ValidationError):
        A2AMessage(sender="", receiver="verifier", action="verify")

    with pytest.raises(ValidationError):
        A2AMessage(sender="builder", receiver="", action="verify")


def test_a2a_policy_gated_send_allows_delegation():
    adapter = A2AAdapter()
    message = A2AMessage(sender="builder", receiver="verifier", action="verify", payload={"capability": "pytest"})

    result = adapter.send(message)

    assert result["sent"] is True
    assert adapter.sent_messages == [message]
    assert result["evidence"]["policy_decision"]["allow"] is True


def test_a2a_policy_gated_send_denies_unauthorized_capability():
    adapter = A2AAdapter()
    message = A2AMessage(sender="builder", receiver="verifier", action="delegate", payload={"capability": "network"})

    result = adapter.send(message)

    assert result["sent"] is False
    assert adapter.sent_messages == []
    assert result["evidence"]["policy_decision"]["allow"] is False
    assert "delegation capability denied" in result["evidence"]["policy_decision"]["denials"]
