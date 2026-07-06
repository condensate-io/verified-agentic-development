import pytest
from pydantic import ValidationError

from vad.swarm.agents import AgentCard, AgentRole


def test_agent_card_validates_capabilities_roles_memory_and_autonomy():
    card = AgentCard(
        agent_id="builder-1",
        role=AgentRole.BUILDER,
        capabilities=["modify_code", "run_tests"],
        model_tiers=["tier1"],
        memory_scopes=["project"],
        autonomy_limit=2,
    )

    assert card.role == AgentRole.BUILDER
    assert card.memory_scopes == ["project"]
    assert card.autonomy_limit == 2


def test_invalid_role_capability_fails_closed():
    with pytest.raises(ValidationError):
        AgentCard(
            agent_id="builder-1",
            role=AgentRole.BUILDER,
            capabilities=["approve_release"],
            model_tiers=["tier1"],
            autonomy_limit=2,
        )


def test_zero_autonomy_builder_fails_closed():
    with pytest.raises(ValidationError):
        AgentCard(
            agent_id="builder-1",
            role=AgentRole.BUILDER,
            capabilities=["modify_code"],
            model_tiers=["tier1"],
            autonomy_limit=0,
        )
