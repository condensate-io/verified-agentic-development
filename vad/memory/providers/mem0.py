from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

from vad.memory.capabilities import MemoryCapabilities, ProviderHealth
from vad.memory.config import MemoryProviderConfig
from vad.memory.contracts import (
    AdvisoryContext,
    InferredLesson,
    LessonReviewStatus,
    MemoryQuery,
    ObservedFact,
)
from vad.memory.providers.generic_endpoint import (
    HttpTransport,
    RemoteEndpointError,
    build_endpoint_url,
    classify_lessons,
    default_http_transport,
    invoke_http_transport,
    resolve_auth_headers,
)


def _fact_to_mem0_payload(fact: ObservedFact, project_id: str) -> dict[str, Any]:
    return {
        "messages": [{"role": "system", "content": fact.summary}],
        "user_id": project_id,
        "agent_id": "vad",
        "metadata": {
            "record_kind": "observed_fact",
            "fact_id": fact.fact_id,
            "fact_type": fact.fact_type.value,
            "run_id": fact.run_id,
            "eip_digest": fact.eip_digest,
            "source_evidence_digest": fact.source_evidence_digest,
            "risk_tier": fact.risk_tier,
            "domain_tags": list(fact.domain_tags),
            "summary": fact.summary,
            "observed_at": fact.observed_at.isoformat(),
            "authoritative_source": fact.authoritative_source,
            "fact_metadata": dict(fact.metadata),
        },
    }


def _lesson_to_mem0_metadata(lesson: InferredLesson) -> dict[str, Any]:
    return {
        "record_kind": "inferred_lesson",
        "lesson_id": lesson.lesson_id,
        "title": lesson.title,
        "recommendation": lesson.recommendation,
        "proposed_rule_type": lesson.proposed_rule_type.value,
        "derived_from_fact_ids": list(lesson.derived_from_fact_ids),
        "domain_tags": list(lesson.domain_tags),
        "confidence": lesson.confidence,
        "review_status": lesson.review_status.value,
        "reviewed_by": lesson.reviewed_by,
        "reviewed_at": lesson.reviewed_at.isoformat() if lesson.reviewed_at else None,
        "promotion_record_id": lesson.promotion_record_id,
    }


def _lesson_to_mem0_payload(lesson: InferredLesson, project_id: str) -> dict[str, Any]:
    return {
        "messages": [
            {
                "role": "assistant",
                "content": f"{lesson.title}: {lesson.recommendation}",
            }
        ],
        "user_id": project_id,
        "agent_id": "vad",
        "metadata": _lesson_to_mem0_metadata(lesson),
    }


def _extract_fact_from_mem0_item(item: Any) -> ObservedFact | None:
    if not isinstance(item, dict):
        return None
    meta = item.get("metadata")
    if not isinstance(meta, dict):
        return None
    if meta.get("record_kind") != "observed_fact":
        return None

    fact_dict = {
        "fact_id": meta.get("fact_id") or item.get("id"),
        "fact_type": meta.get("fact_type"),
        "run_id": meta.get("run_id"),
        "eip_digest": meta.get("eip_digest"),
        "source_evidence_digest": meta.get("source_evidence_digest"),
        "risk_tier": meta.get("risk_tier"),
        "domain_tags": meta.get("domain_tags", []),
        "summary": meta.get("summary") or item.get("memory") or "",
        "metadata": meta.get("fact_metadata", {}),
        "authoritative_source": meta.get("authoritative_source", "vad"),
    }
    if meta.get("observed_at") is not None:
        fact_dict["observed_at"] = meta["observed_at"]
    try:
        return ObservedFact.model_validate(fact_dict)
    except Exception:
        return None


def _extract_lesson_from_mem0_item(item: Any) -> InferredLesson | None:
    if not isinstance(item, dict):
        return None
    meta = item.get("metadata")
    if not isinstance(meta, dict):
        return None
    if meta.get("record_kind") != "inferred_lesson":
        return None

    lesson_dict: dict[str, Any] = {
        "lesson_id": meta.get("lesson_id") or item.get("id"),
        "title": meta.get("title") or item.get("memory") or "",
        "recommendation": meta.get("recommendation") or item.get("memory") or "",
        "proposed_rule_type": meta.get("proposed_rule_type", "general_guidance"),
        "derived_from_fact_ids": meta.get("derived_from_fact_ids", []),
        "domain_tags": meta.get("domain_tags", []),
        "confidence": meta.get("confidence", 0.5),
        "review_status": meta.get("review_status", LessonReviewStatus.PROPOSED.value),
        "reviewed_by": meta.get("reviewed_by"),
        "reviewed_at": meta.get("reviewed_at"),
        "promotion_record_id": meta.get("promotion_record_id"),
    }
    try:
        return InferredLesson.model_validate(lesson_dict)
    except Exception:
        return None


def _extract_mem0_items(response: dict[str, Any]) -> list[dict[str, Any]]:
    raw = response.get("results", response.get("memories", []))
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, dict)]


class Mem0MemoryProvider:
    """Mem0 REST API memory provider adapter with strict fact/lesson metadata isolation."""

    name: str = "mem0"

    def __init__(
        self,
        config: MemoryProviderConfig,
        http_transport: HttpTransport | None = None,
    ) -> None:
        self.config = config
        self._http_transport = http_transport or default_http_transport
        self._base_url = config.endpoint_url
        self._lesson_memory_ids: dict[str, str] = {}

    def _url(self, route_key: str, default_path: str, query_params: dict[str, Any] | None = None) -> str:
        route = self.config.extra_endpoints.get(route_key, default_path)
        return build_endpoint_url(self._base_url, route, query_params=query_params)

    def _request(
        self,
        method: str,
        route_key: str,
        default_path: str,
        *,
        payload: dict[str, Any] | None = None,
        query_params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = self._url(route_key, default_path, query_params=query_params)
        headers = resolve_auth_headers(self.config)
        return invoke_http_transport(
            self._http_transport,
            method,
            url,
            payload,
            headers,
            self.config.timeout_seconds,
        )

    def capabilities(self) -> MemoryCapabilities:
        return MemoryCapabilities(
            fact_storage=True,
            lesson_storage=True,
            scoped_query=True,
            health_check=True,
            semantic_search=True,
            causal_graph=False,
            contradiction_resolution=False,
            context_consolidation=False,
            provenance_traversal=False,
            supported_transports=["http"],
        )

    def health(self) -> ProviderHealth:
        started = time.perf_counter()
        try:
            response = self._request("GET", "health", "/health")
            elapsed_ms = round((time.perf_counter() - started) * 1000.0, 3)
            healthy = bool(response.get("healthy", response.get("status", "ok") == "ok"))
            detail = str(response.get("detail") or response.get("status") or "mem0 ok")
            return ProviderHealth(
                healthy=healthy,
                provider_name=self.name,
                latency_ms=elapsed_ms,
                detail=detail,
            )
        except Exception as exc:
            if not self.config.fail_open_on_unavailable:
                raise
            elapsed_ms = round((time.perf_counter() - started) * 1000.0, 3)
            return ProviderHealth(
                healthy=False,
                provider_name=self.name,
                latency_ms=elapsed_ms,
                detail=str(exc),
            )

    def record_fact(self, fact: ObservedFact) -> str:
        payload = _fact_to_mem0_payload(fact, self.config.project_id)
        try:
            self._request("POST", "memories", "/v1/memories/", payload=payload)
            return fact.fact_id
        except Exception:
            if not self.config.fail_open_on_unavailable:
                raise
            return fact.fact_id

    def record_lesson(self, lesson: InferredLesson) -> str:
        payload = _lesson_to_mem0_payload(lesson, self.config.project_id)
        try:
            response = self._request("POST", "memories", "/v1/memories/", payload=payload)
            mem_id = response.get("id")
            if not mem_id:
                items = _extract_mem0_items(response)
                if items:
                    mem_id = items[0].get("id")
            if mem_id:
                self._lesson_memory_ids[lesson.lesson_id] = str(mem_id)
            return lesson.lesson_id
        except Exception:
            if not self.config.fail_open_on_unavailable:
                raise
            return lesson.lesson_id

    def update_lesson_status(
        self,
        lesson_id: str,
        status: LessonReviewStatus,
        reviewed_by: str,
        promotion_record_id: str | None = None,
    ) -> InferredLesson:
        try:
            response = self._request(
                "GET",
                "memories",
                "/v1/memories/",
                query_params={"user_id": self.config.project_id},
            )
        except RemoteEndpointError as exc:
            if exc.status_code == 404:
                raise KeyError(f"Lesson not found in Mem0: {lesson_id}") from exc
            raise

        matched_lesson: InferredLesson | None = None
        matched_mem_id: str | None = self._lesson_memory_ids.get(lesson_id)
        for item in _extract_mem0_items(response):
            parsed = _extract_lesson_from_mem0_item(item)
            if parsed is not None and parsed.lesson_id == lesson_id:
                matched_lesson = parsed
                if item.get("id"):
                    matched_mem_id = str(item["id"])
                break

        if matched_lesson is None:
            raise KeyError(f"Lesson not found in Mem0: {lesson_id}")

        payload_dict = matched_lesson.model_dump()
        payload_dict["review_status"] = status
        payload_dict["reviewed_by"] = reviewed_by
        payload_dict["reviewed_at"] = datetime.now(timezone.utc)
        if promotion_record_id is not None:
            payload_dict["promotion_record_id"] = promotion_record_id

        updated = InferredLesson.model_validate(payload_dict)
        target_id = matched_mem_id or lesson_id
        update_payload = {
            "text": f"{updated.title}: {updated.recommendation}",
            "metadata": _lesson_to_mem0_metadata(updated),
        }
        self._request(
            "PUT",
            "lesson_status",
            f"/v1/memories/{target_id}/",
            payload=update_payload,
        )
        return updated

    def query(self, query: MemoryQuery) -> AdvisoryContext:
        search_payload: dict[str, Any] = {
            "query": query.query_text,
            "user_id": self.config.project_id,
            "limit": query.limit,
        }
        if query.domain_tags:
            search_payload["filters"] = {"domain_tags": query.domain_tags}

        try:
            response = self._request(
                "POST",
                "search",
                "/v1/memories/search/",
                payload=search_payload,
            )
        except Exception as exc:
            if not self.config.fail_open_on_unavailable:
                raise
            return AdvisoryContext(
                provider_name=self.name,
                facts=[],
                approved_lessons=[],
                advisory_unapproved_lessons=[],
                degraded=True,
                degraded_reason=str(exc),
            )

        facts: list[ObservedFact] = []
        lessons: list[InferredLesson] = []
        for item in _extract_mem0_items(response):
            fact = _extract_fact_from_mem0_item(item)
            if fact is not None:
                if not query.fact_types or fact.fact_type in query.fact_types:
                    facts.append(fact)
                continue
            lesson = _extract_lesson_from_mem0_item(item)
            if lesson is not None:
                lessons.append(lesson)

        approved, unapproved = classify_lessons(
            lessons,
            include_unapproved=query.include_unapproved_lessons,
            limit=query.limit,
        )

        return AdvisoryContext(
            provider_name=self.name,
            facts=facts[: query.limit],
            approved_lessons=approved,
            advisory_unapproved_lessons=unapproved,
        )

    def list_facts(self, limit: int = 50) -> list[ObservedFact]:
        if limit <= 0:
            return []
        try:
            response = self._request(
                "GET",
                "memories",
                "/v1/memories/",
                query_params={"user_id": self.config.project_id, "limit": limit},
            )
        except Exception:
            if not self.config.fail_open_on_unavailable:
                raise
            return []

        facts: list[ObservedFact] = []
        for item in _extract_mem0_items(response):
            fact = _extract_fact_from_mem0_item(item)
            if fact is not None:
                facts.append(fact)
        return facts[:limit]

    def list_lessons(
        self,
        status: LessonReviewStatus | None = None,
        limit: int = 50,
    ) -> list[InferredLesson]:
        if limit <= 0:
            return []
        try:
            response = self._request(
                "GET",
                "memories",
                "/v1/memories/",
                query_params={"user_id": self.config.project_id, "limit": limit},
            )
        except Exception:
            if not self.config.fail_open_on_unavailable:
                raise
            return []

        lessons: list[InferredLesson] = []
        for item in _extract_mem0_items(response):
            lesson = _extract_lesson_from_mem0_item(item)
            if lesson is not None and (status is None or lesson.review_status == status):
                lessons.append(lesson)
        return lessons[:limit]
