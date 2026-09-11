import pytest
from vad.decision.lexical_router import BM25Router, SkillDescriptor
from vad.decision.semantic_router import SemanticRouter
from vad.decision.hybrid_router import HybridRouter, CostTier


@pytest.fixture
def test_corpus():
    return [
        SkillDescriptor(
            id="skill-sip-signaling",
            name="SIP Signaling Protocol Specialist",
            description="Troubleshoots SIP INVITE, BYE, CANCEL, ACK, SDP negotiation, and OpenSIPS proxies.",
            keywords=["sip", "invite", "sdp", "opensips", "proxy"],
            domain="signaling",
            target_agent="sip-signaling-agent",
        ),
        SkillDescriptor(
            id="skill-kubernetes-iac",
            name="Kubernetes Platform Infra",
            description="Terraform, Helm charts, k8s manifests, pod scheduling, and ingress gateways.",
            keywords=["k8s", "kubernetes", "helm", "terraform", "ingress"],
            domain="infrastructure",
            target_agent="k8s-platform-agent",
        ),
    ]


def test_hybrid_router_routes_via_tier0_lexical(test_corpus):
    router = HybridRouter()
    router.index(test_corpus)

    # Exact keyword match triggers Tier 0
    decision = router.route("Analyze SIP INVITE and SDP negotiation failure in OpenSIPS proxy")
    assert decision.is_routed is True
    assert decision.match_id == "skill-sip-signaling"
    assert decision.cost_tier == CostTier.TIER_0_LEXICAL
    assert decision.routing_path == "lexical_direct"


def test_hybrid_router_routes_via_tier1_semantic_fallback(test_corpus):
    router = HybridRouter(lexical_threshold=0.8, semantic_threshold=0.2)
    router.index(test_corpus)

    # Conceptual query with synonyms ("cluster deployment containers orchestration")
    decision = router.route("Deploy application pods and manage cluster ingress services")
    assert decision.is_routed is True
    assert decision.match_id == "skill-kubernetes-iac"
    assert decision.cost_tier in (CostTier.TIER_0_LEXICAL, CostTier.TIER_1_SEMANTIC)


def test_hybrid_router_unresolved_query(test_corpus):
    router = HybridRouter()
    router.index(test_corpus)

    decision = router.route("Make an apple pie recipe")
    assert decision.is_routed is False
    assert decision.routing_path == "unresolved"
    assert decision.cost_tier == CostTier.TIER_2_LLM
