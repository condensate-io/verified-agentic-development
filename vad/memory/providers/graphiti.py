from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import Any

from vad.memory.capabilities import MemoryCapabilities, ProviderHealth
from vad.memory.config import MemoryProviderConfig, MemoryTransport
from vad.memory.contracts import (
    AdvisoryContext,
    InferredLesson,
    LessonReviewStatus,
    MemoryQuery,
    ObservedFact,
)
from vad.memory.providers.generic_endpoint import (
    HttpTransport,
    McpTransport,
    RemoteEndpointError,
    build_endpoint_url,
    classify_lessons,
    default_http_transport,
    default_mcp_transport,
    invoke_http_transport,
    invoke_mcp_transport,
    resolve_auth_headers,
)

ENTITY_TYPE_FACT = "vad_observed_fact"
ENTITY_TYPE_LESSON = "vad_inferred_lesson"


def _fact_to_episode_payload(fact: ObservedFact, project_id: str) -> dict[str, Any]:
    fact_data = fact.model_dump(mode="json")
    return {
        "group_id": project_id,
        "name": f"fact:{fact.fact_id}",
        "entity_type": ENTITY_TYPE_FACT,
        "episode_body": json.dumps(
            {
                "entity_type": ENTITY_TYPE_FACT,
                "fact": fact_data,
            },
            sort_keys=True,
        ),
        "source": "json",
        "source_description": f"{ENTITY_TYPE_FACT}:{fact.fact_type.value}",
        "reference_time": fact.observed_at.isoformat(),
        "metadata": {
            "entity_type": ENTITY_TYPE_FACT,
            "fact_id": fact.fact_id,
            "fact_type": fact.fact_type.value,
            "run_id": fact.run_id,
            "eip_digest": fact.eip_digest,
            "source_evidence_digest": fact.source_evidence_digest,
            "risk_tier": fact.risk_tier,
            "domain_tags": list(fact.domain_tags),
            "authoritative_source": fact.authoritative_source,
        },
    }


def _lesson_to_episode_payload(lesson: InferredLesson, project_id: str) -> dict[str, Any]:
    lesson_data = lesson.model_dump(mode="json")
    return {
        "group_id": project_id,
        "name": f"lesson:{lesson.lesson_id}",
        "entity_type": ENTITY_TYPE_LESSON,
        "episode_body": json.dumps(
            {
                "entity_type": ENTITY_TYPE_LESSON,
                "lesson": lesson_data,
            },
            sort_keys=True,
        ),
        "source": "json",
        "source_description": f"{ENTITY_TYPE_LESSON}:{lesson.proposed_rule_type.value}",
        "reference_time": (
            lesson.reviewed_at.isoformat()
            if lesson.reviewed_at
            else datetime.now(timezone.utc).isoformat()
        ),
        "metadata": {
            "entity_type": ENTITY_TYPE_LESSON,
            "lesson_id": lesson.lesson_id,
            "proposed_rule_type": lesson.proposed_rule_type.value,
            "derived_from_fact_ids": list(lesson.derived_from_fact_ids),
            "domain_tags": list(lesson.domain_tags),
            "confidence": lesson.confidence,
            "review_status": lesson.review_status.value,
            "reviewed_by": lesson.reviewed_by,
            "reviewed_at": lesson.reviewed_at.isoformat() if lesson.reviewed_at else None,
            "promotion_record_id": lesson.promotion_record_id,
        },
    }


def _decode_episode_body(item: dict[str, Any]) -> dict[str, Any]:
    raw_body = item.get("episode_body")
    if isinstance(raw_body, dict):
        return raw_body
    if isinstance(raw_body, str) and raw_body.strip():
        try:
            parsed = json.loads(raw_body)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            return {}
    return {}


def _resolve_entity_type(item: dict[str, Any], body: dict[str, Any]) -> str | None:
    meta = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    entity_type = (
        item.get("entity_type")
        or body.get("entity_type")
        or meta.get("entity_type")
    )
    return str(entity_type) if entity_type else None


def _extract_fact_from_episode(item: Any) -> ObservedFact | None:
    if not isinstance(item, dict):
        return None
    body = _decode_episode_body(item)
    if _resolve_entity_type(item, body) != ENTITY_TYPE_FACT:
        return None
    candidate = body.get("fact") if isinstance(body.get("fact"), dict) else item.get("fact")
    if not isinstance(candidate, dict):
        meta = item.get("metadata")
        if isinstance(meta, dict) and "fact_id" in meta:
            candidate = {
                **meta,
                "summary": item.get("summary") or item.get("content") or "",
            }
    if not isinstance(candidate, dict):
        return None
    try:
        return ObservedFact.model_validate(candidate)
    except Exception:
        return None


def _extract_lesson_from_episode(item: Any) -> InferredLesson | None:
    if not isinstance(item, dict):
        return None
    body = _decode_episode_body(item)
    if _resolve_entity_type(item, body) != ENTITY_TYPE_LESSON:
        return None
    candidate = body.get("lesson") if isinstance(body.get("lesson"), dict) else item.get("lesson")
    if not isinstance(candidate, dict):
        meta = item.get("metadata")
        if isinstance(meta, dict) and "lesson_id" in meta:
            candidate = {
                **meta,
                "title": item.get("title") or item.get("name") or "",
                "recommendation": item.get("recommendation") or item.get("content") or "",
            }
    if not isinstance(candidate, dict):
        return None
    try:
        return InferredLesson.model_validate(candidate)
    except Exception:
        return None


def _extract_graphiti_items(response: dict[str, Any]) -> list[dict[str, Any]]:
    collected: list[dict[str, Any]] = []
    for key in ("episodes", "facts", "nodes", "edges", "results"):
        bucket = response.get(key)
        if isinstance(bucket, list):
            for entry in bucket:
                if isinstance(entry, dict):
                    collected.append(entry)
    return collected


class GraphitiMemoryProvider:
    """Graphiti temporal knowledge-graph adapter with strict entity_type isolation."""

    name: str = "graphiti"

    def __init__(
        self,
        config: MemoryProviderConfig,
        http_transport: HttpTransport | None = None,
        mcp_transport: McpTransport | None = None,
    ) -> None:
        self.config = config
        self._http_transport = http_transport or default_http_transport
        self._mcp_transport = mcp_transport
        self._base_url = config.endpoint_url

    def _use_mcp(self) -> bool:
        return self.config.transport == MemoryTransport.MCP or (
            self.config.transport == MemoryTransport.BOTH and not self._base_url
        )

    def _url(
        self,
        route_key: str,
        default_path: str,
        query_params: dict[str, Any] | None = None,
    ) -> str:
        route = self.config.extra_endpoints.get(route_key, default_path)
        return build_endpoint_url(self._base_url, route, query_params=query_params)

    def _http_request(
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

    def _mcp_request(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        headers = resolve_auth_headers(self.config)
        payload = {"group_id": self.config.project_id, **arguments}
        if self._mcp_transport is not None:
            return invoke_mcp_transport(
                self._mcp_transport,
                tool_name,
                payload,
                headers,
                self.config.timeout_seconds,
            )
        if not self.config.mcp_endpoint:
            raise RemoteEndpointError("No mcp_endpoint configured for Graphiti MCP transport")
        return default_mcp_transport(
            self.config.mcp_endpoint,
            tool_name,
            payload,
            headers,
            self.config.timeout_seconds,
        )

    def _call(
        self,
        method: str,
        route_key: str,
        default_path: str,
        mcp_tool: str,
        *,
        payload: dict[str, Any] | None = None,
        query_params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if self._use_mcp():
            return self._mcp_request(mcp_tool, payload or query_params or {})
        try:
            return self._http_request(
                method,
                route_key,
                default_path,
                payload=payload,
                query_params=query_params,
            )
        except Exception:
            if (
                self.config.transport == MemoryTransport.BOTH
                and (self._mcp_transport is not None or self.config.mcp_endpoint)
            ):
                return self._mcp_request(mcp_tool, payload or query_params or {})
            raise

    def capabilities(self) -> MemoryCapabilities:
        return MemoryCapabilities(
            fact_storage=True,
            lesson_storage=True,
            scoped_query=True,
            health_check=True,
            semantic_search=True,
            causal_graph=True,
            contradiction_resolution=False,
            context_consolidation=False,
            provenance_traversal=True,
            supported_transports=["http", "mcp"],
        )

    def health(self) -> ProviderHealth:
        started = time.perf_counter()
        try:
            response = self._call(
                "GET",
                "health",
                "/healthcheck",
                "graphiti_healthcheck",
            )
            elapsed_ms = round((time.perf_counter() - started) * 1000.0, 3)
            healthy = bool(
                response.get("healthy", response.get("status", "healthy") in {"ok", "healthy"})
            )
            detail = str(response.get("detail") or response.get("status") or "graphiti ok")
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
        payload = _fact_to_episode_payload(fact, self.config.project_id)
        try:
            self._call("POST", "episodes", "/episodes", "add_episode", payload=payload)
            return fact.fact_id
        except Exception:
            if not self.config.fail_open_on_unavailable:
                raise
            return fact.fact_id

    def record_lesson(self, lesson: InferredLesson) -> str:
        payload = _lesson_to_episode_payload(lesson, self.config.project_id)
        try:
            self._call("POST", "episodes", "/episodes", "add_episode", payload=payload)
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
            response = self._call(
                "GET",
                "episodes",
                "/episodes",
                "get_episodes",
                query_params={"group_id": self.config.project_id, "limit": 200},
            )
        except RemoteEndpointError as exc:
            if exc.status_code == 404:
                raise KeyError(f"Lesson not found in Graphiti: {lesson_id}") from exc
            raise

        latest_lesson: InferredLesson | None = None
        for item in _extract_graphiti_items(response):
            parsed = _extract_lesson_from_episode(item)
            if parsed is not None and parsed.lesson_id == lesson_id:
                latest_lesson = parsed

        if latest_lesson is None:
            raise KeyError(f"Lesson not found in Graphiti: {lesson_id}")

        payload_dict = latest_lesson.model_dump()
        payload_dict["review_status"] = status
        payload_dict["reviewed_by"] = reviewed_by
        payload_dict["reviewed_at"] = datetime.now(timezone.utc)
        if promotion_record_id is not None:
            payload_dict["promotion_record_id"] = promotion_record_id

        updated = InferredLesson.model_validate(payload_dict)
        episode_payload = _lesson_to_episode_payload(updated, self.config.project_id)
        self._call(
            "POST",
            "episodes",
            "/episodes",
            "add_episode",
            payload=episode_payload,
        )
        return updated

    def query(self, query: MemoryQuery) -> AdvisoryContext:
        search_payload: dict[str, Any] = {
            "query": query.query_text,
            "group_ids": [self.config.project_id],
            "max_facts": query.limit,
            "limit": query.limit,
            "domain_tags": query.domain_tags,
        }
        try:
            response = self._call(
                "POST",
                "search",
                "/search",
                "search_memory_facts",
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

        facts_by_id: dict[str, ObservedFact] = {}
        lessons_by_id: dict[str, InferredLesson] = {}
        for item in _extract_graphiti_items(response):
            fact = _extract_fact_from_episode(item)
            if fact is not None:
                if not query.fact_types or fact.fact_type in query.fact_types:
                    facts_by_id[fact.fact_id] = fact
                continue
            lesson = _extract_lesson_from_episode(item)
            if lesson is not None:
                lessons_by_id[lesson.lesson_id] = lesson

        approved, unapproved = classify_lessons(
            list(lessons_by_id.values()),
            include_unapproved=query.include_unapproved_lessons,
            limit=query.limit,
        )

        return AdvisoryContext(
            provider_name=self.name,
            facts=list(facts_by_id.values())[: query.limit],
            approved_lessons=approved,
            advisory_unapproved_lessons=unapproved,
        )

    def list_facts(self, limit: int = 50) -> list[ObservedFact]:
        if limit <= 0:
            return []
        try:
            response = self._call(
                "GET",
                "episodes",
                "/episodes",
                "get_episodes",
                query_params={"group_id": self.config.project_id, "limit": limit},
            )
        except Exception:
            if not self.config.fail_open_on_unavailable:
                raise
            return []

        facts_by_id: dict[str, ObservedFact] = {}
        for item in _extract_graphiti_items(response):
            fact = _extract_fact_from_episode(item)
            if fact is not None:
                facts_by_id[fact.fact_id] = fact
        return list(facts_by_id.values())[:limit]

    def list_lessons(
        self,
        status: LessonReviewStatus | None = None,
        limit: int = 50,
    ) -> list[InferredLesson]:
        if limit <= 0:
            return []
        try:
            response = self._call(
                "GET",
                "episodes",
                "/episodes",
                "get_episodes",
                query_params={"group_id": self.config.project_id, "limit": limit},
            )
        except Exception:
            if not self.config.fail_open_on_unavailable:
                raise
            return []

        lessons_by_id: dict[str, InferredLesson] = {}
        for item in _extract_graphiti_items(response):
            lesson = _extract_lesson_from_episode(item)
            if lesson is not None:
                lessons_by_id[lesson.lesson_id] = lesson

        filtered = [
            lesson
            for lesson in lessons_by_id.values()
            if status is None or lesson.review_status == status
        ]
        return filtered[:limit]
