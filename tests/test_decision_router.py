import pytest
from vad.decision.hybrid_router import HybridRouter
from vad.decision.lexical_router import SkillDescriptor
from vad.decision.router import OperatorRouter, OperatorRoutingPolicy


@pytest.fixture
def operator_router():
    corpus = [
        SkillDescriptor(
            id="skill-sec",
            name="Security Specialist",
            description="Security vulnerability audit, auth checks, and crypto",
            keywords=["security", "vulnerability", "auth", "crypto"],
            domain="security",
            target_agent="sec-agent",
        )
    ]
    hybrid = HybridRouter()
    hybrid.index(corpus)
    policy = OperatorRoutingPolicy(
        domain_runners={"security": ["claude", "aider"], "general": ["claude", "cursor"]},
        disallowed_runners=["copilot"],
    )
    return OperatorRouter(hybrid, policy)


def test_operator_router_domain_mapping(operator_router):
    plan = operator_router.plan_routing("Audit security vulnerability in crypto module")
    assert plan.domain == "security"
    assert plan.primary_runner == "claude"
    assert "aider" in plan.fallback_runners
    assert plan.is_approved is True


def test_operator_router_respects_disallowed_runner(operator_router):
    # Requesting disallowed runner copilot gets blocked and falls back to allowed runner
    plan = operator_router.plan_routing("General clean up", requested_runner="copilot")
    assert plan.primary_runner != "copilot"
    assert "blocked by policy" in plan.reason


def test_operator_router_explicit_override_allowed(operator_router):
    plan = operator_router.plan_routing("Security fix", requested_runner="aider")
    assert plan.primary_runner == "aider"
    assert "Explicitly routed" in plan.reason
