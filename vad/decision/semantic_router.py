"""Semantic Router: Embedding-based semantic search wrapper with remote service and local fallback."""

import math
import re
from typing import Dict, List, Optional
import httpx
from pydantic import BaseModel
from vad.decision.lexical_router import SkillDescriptor


class SemanticMatchResult(BaseModel):
    match_id: Optional[str] = None
    target_agent: Optional[str] = None
    domain: Optional[str] = None
    similarity: float = 0.0
    confidence: float = 0.0
    is_confident: bool = False
    provider: str = "local"


class SemanticRouter:
    """Semantic router that queries a semantic classifier service (e.g. port 8099)

    with built-in local vector similarity fallback for deterministic offline execution.
    """

    def __init__(
        self,
        service_url: Optional[str] = "http://localhost:8099/classify",
        timeout: float = 2.0,
        fallback_local: bool = True,
    ):
        self.service_url = service_url
        self.timeout = timeout
        self.fallback_local = fallback_local
        self.descriptors: Dict[str, SkillDescriptor] = {}
        self.doc_vectors: Dict[str, Dict[str, float]] = {}

    def _char_ngram_vector(self, text: str, n: int = 3) -> Dict[str, float]:
        clean = re.sub(r"[^a-zA-Z0-9 ]", "", text.lower())
        vec: Dict[str, float] = {}
        if len(clean) < n:
            vec[clean] = 1.0
            return vec
        for i in range(len(clean) - n + 1):
            gram = clean[i : i + n]
            vec[gram] = vec.get(gram, 0.0) + 1.0
        # Normalize
        norm = math.sqrt(sum(v * v for v in vec.values()))
        if norm > 0:
            for k in vec:
                vec[k] /= norm
        return vec

    def _cosine_similarity(self, v1: Dict[str, float], v2: Dict[str, float]) -> float:
        score = 0.0
        for k, val in v1.items():
            if k in v2:
                score += val * v2[k]
        return score

    def index(self, descriptors: List[SkillDescriptor]) -> None:
        self.descriptors = {d.id: d for d in descriptors}
        self.doc_vectors = {}
        for d in descriptors:
            text = f"{d.name} {d.description} {' '.join(d.keywords)} {d.domain}"
            self.doc_vectors[d.id] = self._char_ngram_vector(text)

    def _route_remote(self, query: str) -> Optional[SemanticMatchResult]:
        if not self.service_url:
            return None
        try:
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.post(self.service_url, json={"query": query})
                if resp.status_code == 200:
                    data = resp.json()
                    match_id = data.get("match_id")
                    similarity = float(data.get("similarity", 0.0))
                    desc = self.descriptors.get(match_id)
                    return SemanticMatchResult(
                        match_id=match_id,
                        target_agent=desc.target_agent if desc else data.get("target_agent"),
                        domain=desc.domain if desc else data.get("domain"),
                        similarity=similarity,
                        confidence=similarity,
                        is_confident=similarity >= 0.5,
                        provider="remote-service",
                    )
        except Exception:
            # Service not reachable, fallback to local
            return None
        return None

    def route(self, query: str, threshold: float = 0.4) -> SemanticMatchResult:
        # Try remote first if configured
        if self.service_url:
            remote_res = self._route_remote(query)
            if remote_res is not None:
                return remote_res

        if not self.fallback_local or not self.descriptors:
            return SemanticMatchResult(is_confident=False, provider="none")

        # Local vector fallback
        q_vec = self._char_ngram_vector(query)
        best_id: Optional[str] = None
        best_sim = 0.0

        for doc_id, d_vec in self.doc_vectors.items():
            sim = self._cosine_similarity(q_vec, d_vec)
            if sim > best_sim:
                best_sim = sim
                best_id = doc_id

        if best_id and best_sim >= threshold:
            desc = self.descriptors[best_id]
            return SemanticMatchResult(
                match_id=best_id,
                target_agent=desc.target_agent,
                domain=desc.domain,
                similarity=round(best_sim, 4),
                confidence=round(best_sim, 4),
                is_confident=True,
                provider="local-vector",
            )

        return SemanticMatchResult(
            match_id=best_id,
            similarity=round(best_sim, 4),
            confidence=round(best_sim, 4),
            is_confident=False,
            provider="local-vector",
        )
