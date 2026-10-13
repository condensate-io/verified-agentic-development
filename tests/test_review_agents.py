import pytest
from vad.plugins.claude_code.review_agents import (
    ReviewAgentManager,
    ReviewAgentType,
    ReviewFinding,
    ReviewAssessment,
)
from vad.review.cross_harness import CrossHarnessReviewRouter, SeparationOfDutiesViolation


def test_review_agent_spec():
    mgr = ReviewAgentManager()
    spec = mgr.get_agent_spec(ReviewAgentType.SECURITY, harness="copilot")
    assert spec.agent_type == ReviewAgentType.SECURITY
    assert spec.harness == "copilot"
    assert "secret leaks" in spec.system_prompt


def test_launch_review_success():
    mgr = ReviewAgentManager()
    diff = "def add(a, b): return a + b"
    assessment = mgr.launch_review(
        artifact_id="art-101",
        builder_harness="claude",
        diff_content=diff,
    )
    assert isinstance(assessment, ReviewAssessment)
    assert assessment.approved is True
    assert assessment.cross_harness_result is not None
    assert assessment.cross_harness_result.separation_of_duties_verified is True


def test_launch_review_security_blocker():
    mgr = ReviewAgentManager()
    diff = "import os\nos.system('rm -rf /')"
    assessment = mgr.launch_review(
        artifact_id="art-dangerous",
        builder_harness="claude",
        diff_content=diff,
    )
    assert assessment.approved is False
    assert any(f.severity == "blocker" for f in assessment.findings)
    assert "rejected" in assessment.summary


def test_launch_review_separation_of_duties_guard():
    # Force mock router that tries to pair builder with itself
    class MockRouter(CrossHarnessReviewRouter):
        def select_reviewer(self, builder_harness: str) -> str:
            return builder_harness

    mgr = ReviewAgentManager(review_router=MockRouter())
    with pytest.raises(SeparationOfDutiesViolation):
        mgr.launch_review(
            artifact_id="art-self",
            builder_harness="claude",
            diff_content="pass",
        )


def test_select_review_depth_from_mees():
    from vad.plugins.claude_code.review_agents import select_review_depth_from_mees, ReviewDepth

    depth, agents = select_review_depth_from_mees(85)
    assert depth == ReviewDepth.SOLO
    assert len(agents) == 1
    assert agents[0] == ReviewAgentType.CROSS_HARNESS

    depth, agents = select_review_depth_from_mees(62)
    assert depth == ReviewDepth.DOMAIN
    assert len(agents) == 2
    assert ReviewAgentType.DOMAIN in agents

    depth, agents = select_review_depth_from_mees(40)
    assert depth == ReviewDepth.ADVERSARIAL
    assert len(agents) == 3
    assert ReviewAgentType.ADVERSARIAL in agents


def test_agent_card_lifecycle_templates():
    from vad.agents.card import AgentCard, AgentCapabilities

    card = AgentCard(
        name="LifecycleVerifier",
        description="Comprehensive verifier agent",
        role="adversarial_verifier",
        capabilities=AgentCapabilities(tools=["pytest", "opa"], model_tier="tier2", memory_scope="session"),
        builder="alice",
        approver="bob",
        invariants=["INV-01: Zero secret leakage", "INV-02: Separation of duties"],
        proof_obligations=["PO-01: Unit tests passing"],
    )

    md = card.to_markdown()
    assert "# Agent Card: LifecycleVerifier" in md
    assert "INV-01: Zero secret leakage" in md
    assert "PO-01: Unit tests passing" in md

    # EIP 11 sections
    eip_md = AgentCard.render_eip_template(title="Test EIP")
    for section_num in range(1, 12):
        assert f"## {section_num}." in eip_md

    # Verdict template
    verdict_md = AgentCard.render_verdict_template()
    assert "Critical Findings" in verdict_md
    assert "Invariant Verification Checklist" in verdict_md

    # Post-mortem template
    pm_md = AgentCard.render_post_mortem_template()
    assert "VAD Cycle Feedback" in pm_md
    assert "New Invariants" in pm_md
    assert "New Proof Obligations" in pm_md
