import pytest
from vad.decision.hybrid_router import HybridRouter
from vad.decision.lexical_router import SkillDescriptor
from vad.plugins.claude_code.hooks.user_prompt_submit import UserPromptSubmitHook
from vad.policy.autonomy_policy import AutonomyPolicyEvaluator, AutonomyPolicyResource


@pytest.fixture
def submit_hook():
    descriptors = [
        SkillDescriptor(
            id="skill-sip",
            name="SIP Signaling",
            description="Diagnose SIP call flows and INVITE messages",
            keywords=["sip", "invite", "sdp"],
            domain="signaling",
            target_agent="sip-specialist",
        )
    ]
    hybrid = HybridRouter()
    hybrid.index(descriptors)

    policy = AutonomyPolicyResource(
        blocked_tools=["git_push_force", "rm_rf"],
        maturity_level=3,
    )
    evaluator = AutonomyPolicyEvaluator(policy)

    return UserPromptSubmitHook(hybrid_router=hybrid, policy_evaluator=evaluator)


def test_user_prompt_submit_enriches_prompt(submit_hook):
    payload = {"prompt": "Help me debug a SIP INVITE timeout issue in telephony"}
    res = submit_hook.process(payload)

    assert "VAD SYSTEM CONTEXT" in res.enriched_prompt
    assert "Blocked tools" in res.enriched_prompt
    assert "git_push_force" in res.enriched_prompt
    assert "Recommended domain specialist: `skill-sip`" in res.enriched_prompt
    assert res.routing_suggestion["match_id"] == "skill-sip"
    assert "Help me debug a SIP INVITE timeout" in res.enriched_prompt
