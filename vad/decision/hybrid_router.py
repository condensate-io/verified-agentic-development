"""Hybrid Router: Two-tier cost-optimized routing orchestrating lexical BM25 and semantic search."""

from enum import Enum
from typing import List, Optional
from pydantic import BaseModel
from vad.decision.lexical_router import BM25Router, SkillDescriptor
from vad.decision.semantic_router import SemanticRouter


class CostTier(str, Enum):
    TIER_0_LEXICAL = "tier_0_lexical"
    TIER_1_SEMANTIC = "tier_1_semantic"
    TIER_2_LLM = "tier_2_llm"


class HybridRoutingDecision(BaseModel):
    match_id: Optional[str] = None
    target_agent: Optional[str] = None
    domain: Optional[str] = None
    cost_tier: CostTier
    confidence: float = 0.0
    lexical_score: float = 0.0
    semantic_score: float = 0.0
    routing_path: str
    is_routed: bool = False


class HybridRouter:
    """Orchestrates fast-path lexical routing first, escalating to semantic search only when ambiguous."""

    def __init__(
        self,
        lexical_router: Optional[BM25Router] = None,
        semantic_router: Optional[SemanticRouter] = None,
        lexical_threshold: float = 0.5,
        semantic_threshold: float = 0.4,
    ):
        self.lexical = lexical_router or BM25Router()
        self.semantic = semantic_router or SemanticRouter()
        self.lexical_threshold = lexical_threshold
        self.semantic_threshold = semantic_threshold

    def index(self, descriptors: List[SkillDescriptor]) -> None:
        self.lexical.index(descriptors)
        self.semantic.index(descriptors)

    def route(self, query: str) -> HybridRoutingDecision:
        # Step 1: Run fast-path lexical BM25
        lex_res = self.lexical.route(query, threshold=self.lexical_threshold)
        if lex_res.is_confident and lex_res.match_id:
            return HybridRoutingDecision(
                match_id=lex_res.match_id,
                target_agent=lex_res.target_agent,
                domain=lex_res.domain,
                cost_tier=CostTier.TIER_0_LEXICAL,
                confidence=lex_res.confidence,
                lexical_score=lex_res.score,
                semantic_score=0.0,
                routing_path="lexical_direct",
                is_routed=True,
            )

        # Step 2: Lexical failed or low confidence -> fallback to semantic vector search
        sem_res = self.semantic.route(query, threshold=self.semantic_threshold)
        if sem_res.is_confident and sem_res.match_id:
            return HybridRoutingDecision(
                match_id=sem_res.match_id,
                target_agent=sem_res.target_agent,
                domain=sem_res.domain,
                cost_tier=CostTier.TIER_1_SEMANTIC,
                confidence=sem_res.confidence,
                lexical_score=lex_res.score,
                semantic_score=sem_res.similarity,
                routing_path="semantic_fallback",
                is_routed=True,
            )

        # Step 3: Neither could resolve with high confidence
        return HybridRoutingDecision(
            match_id=sem_res.match_id or lex_res.match_id,
            target_agent=sem_res.target_agent or lex_res.target_agent,
            domain=sem_res.domain or lex_res.domain,
            cost_tier=CostTier.TIER_2_LLM,
            confidence=max(lex_res.confidence, sem_res.confidence),
            lexical_score=lex_res.score,
            semantic_score=sem_res.similarity,
            routing_path="unresolved",
            is_routed=False,
        )
