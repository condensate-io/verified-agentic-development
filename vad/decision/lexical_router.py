"""Lexical Router: Fast-path BM25 keyword matching for skill/agent routing."""

import math
import re
from typing import Dict, List, Optional, Set
from pydantic import BaseModel, Field


class SkillDescriptor(BaseModel):
    id: str
    name: str
    description: str
    keywords: List[str] = Field(default_factory=list)
    domain: str = "general"
    target_agent: str = "general-agent"


class LexicalMatchResult(BaseModel):
    match_id: Optional[str] = None
    target_agent: Optional[str] = None
    domain: Optional[str] = None
    score: float = 0.0
    confidence: float = 0.0
    is_confident: bool = False


class BM25Router:
    """Lightweight, deterministic BM25 keyword router against a corpus of skill descriptors."""

    def __init__(self, k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b
        self.descriptors: Dict[str, SkillDescriptor] = {}
        self.doc_tokens: Dict[str, List[str]] = {}
        self.doc_lengths: Dict[str, int] = {}
        self.avg_dl: float = 0.0
        self.idf: Dict[str, float] = {}
        self.vocab: Set[str] = set()

    def _tokenize(self, text: str) -> List[str]:
        # Lowercase and split on non-alphanumeric characters, filtering single-char tokens
        tokens = re.findall(r"\b[a-zA-Z0-9_\-]{2,}\b", text.lower())
        return tokens

    def index(self, descriptors: List[SkillDescriptor]) -> None:
        self.descriptors = {d.id: d for d in descriptors}
        self.doc_tokens = {}
        self.doc_lengths = {}
        self.vocab = set()

        total_length = 0
        df: Dict[str, int] = {}

        for d in descriptors:
            content = f"{d.name} {d.description} {' '.join(d.keywords)} {d.domain} {d.target_agent}"
            tokens = self._tokenize(content)
            self.doc_tokens[d.id] = tokens
            doc_len = len(tokens)
            self.doc_lengths[d.id] = doc_len
            total_length += doc_len

            unique_tokens = set(tokens)
            for token in unique_tokens:
                df[token] = df.get(token, 0) + 1
                self.vocab.add(token)

        n_docs = len(descriptors)
        self.avg_dl = (total_length / n_docs) if n_docs > 0 else 0.0

        # Calculate standard Lucene/BM25 IDF
        self.idf = {}
        for token, doc_count in df.items():
            self.idf[token] = math.log(1.0 + (n_docs - doc_count + 0.5) / (doc_count + 0.5))

    def _score_doc(self, query_tokens: List[str], doc_id: str) -> float:
        tokens = self.doc_tokens.get(doc_id, [])
        dl = self.doc_lengths.get(doc_id, 0)
        if dl == 0 or self.avg_dl == 0:
            return 0.0

        tf_map: Dict[str, int] = {}
        for t in tokens:
            tf_map[t] = tf_map.get(t, 0) + 1

        score = 0.0
        for q in query_tokens:
            if q not in tf_map or q not in self.idf:
                continue
            tf = tf_map[q]
            idf = self.idf[q]
            denom = tf + self.k1 * (1.0 - self.b + self.b * (dl / self.avg_dl))
            score += idf * (tf * (self.k1 + 1.0)) / denom

        return score

    def route_top_k(self, query: str, k: int = 3, threshold: float = 0.4) -> List[LexicalMatchResult]:
        query_tokens = self._tokenize(query)
        if not query_tokens or not self.descriptors:
            return []

        raw_scores: List[tuple[str, float]] = []
        for doc_id in self.descriptors:
            score = self._score_doc(query_tokens, doc_id)
            if score > 0.0:
                raw_scores.append((doc_id, score))

        if not raw_scores:
            return []

        raw_scores.sort(key=lambda x: x[1], reverse=True)
        max_score = raw_scores[0][1]

        results: List[LexicalMatchResult] = []
        for doc_id, score in raw_scores[:k]:
            confidence = min(1.0, score / (max_score + 1e-6)) if max_score > 0 else 0.0
            desc = self.descriptors[doc_id]
            results.append(
                LexicalMatchResult(
                    match_id=doc_id,
                    target_agent=desc.target_agent,
                    domain=desc.domain,
                    score=round(score, 4),
                    confidence=round(confidence, 4),
                    is_confident=(score >= 1.0 and confidence >= threshold),
                )
            )

        return results

    def route(self, query: str, threshold: float = 0.4) -> LexicalMatchResult:
        top = self.route_top_k(query, k=1, threshold=threshold)
        if top and top[0].is_confident:
            return top[0]
        elif top:
            # Low confidence match
            result = top[0]
            result.is_confident = False
            return result
        return LexicalMatchResult(is_confident=False)
