"""Parameterized Memory Provider Conformance Test Suite.

Every memory provider adapter integrated with VAD must pass this suite to prove:
1. Protocol & capability-discovery conformance (`MemoryProviderProtocol`).
2. Strict separation between `ObservedFact` and `InferredLesson`.
3. Approval gating: unapproved lessons never enter `AdvisoryContext.approved_lessons`.
4. Redaction and authority-boundary enforcement through `MemoryGateway`.
5. Graceful offline degradation (VAD keeps operating when memory is unavailable).
"""
from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlparse

import pytest

from vad.memory.authority_guard import MemoryAuthorityGuard, MemoryAuthorityViolationError
from vad.memory.capabilities import MemoryCapabilities, ProviderHealth
from vad.memory.config import MemoryProviderConfig, MemoryProviderKind, MemoryTransport
from vad.memory.contracts import (
    AdvisoryContext,
    FactType,
    InferredLesson,
    LessonReviewStatus,
    MEMORY_INTERFACE_VERSION,
    MemoryQuery,
    ObservedFact,
    ProposedRuleType,
)
from vad.memory.gateway import MemoryGateway
from vad.memory.provider import MemoryProviderProtocol
from vad.memory.providers import (
    CondensateCoreProvider,
    GenericEndpointMemoryProvider,
    GraphitiMemoryProvider,
    LocalMemoryProvider,
    Mem0MemoryProvider,
    NullMemoryProvider,
    create_memory_provider,
)
from vad.memory.redaction import Redactor


EVIDENCE_DIGEST = "c" * 64
EIP_DIGEST = "d" * 64


def _sample_fact(fact_id: str = "fact-conf-1", summary: str = "Auth token expiry defect observed") -> ObservedFact:
    return ObservedFact(
        fact_id=fact_id,
        fact_type=FactType.INCIDENT,
        run_id="run-conf-1",
        eip_digest=EIP_DIGEST,
        source_evidence_digest=EVIDENCE_DIGEST,
        risk_tier="high",
        domain_tags=["auth", "tokens"],
        summary=summary,
    )


def _sample_lesson(
    lesson_id: str = "lesson-conf-1",
    *,
    status: LessonReviewStatus = LessonReviewStatus.PROPOSED,
    reviewed_by: str | None = None,
) -> InferredLesson:
    return InferredLesson(
        lesson_id=lesson_id,
        title="Add auth token expiry proof obligation",
        recommendation="Require unit and security proof for token expiry",
        proposed_rule_type=ProposedRuleType.PROOF_OBLIGATION,
        derived_from_fact_ids=["fact-conf-1"],
        domain_tags=["auth", "tokens"],
        confidence=0.9,
        review_status=status,
        reviewed_by=reviewed_by,
    )


def _make_rest_mock() -> Any:
    """In-memory mock for CondensateCore and GenericEndpoint HTTP REST routes."""
    facts: dict[str, dict[str, Any]] = {}
    lessons: dict[str, dict[str, Any]] = {}

    def handler(method: str, url: str, payload: dict[str, Any] | None, headers: dict[str, str]) -> dict[str, Any]:
        path = urlparse(url).path
        if method == "GET" and path.endswith("/health"):
            return {"status": "ok", "healthy": True}
        if method == "POST" and path.endswith("/facts"):
            assert payload is not None
            fact = payload.get("fact", payload)
            facts[fact["fact_id"]] = fact
            return {"fact_id": fact["fact_id"]}
        if method == "GET" and path.endswith("/facts"):
            return {"facts": list(facts.values())}
        if method == "POST" and path.endswith("/lessons"):
            assert payload is not None
            lesson = payload.get("lesson", payload)
            lessons[lesson["lesson_id"]] = lesson
            return {"lesson_id": lesson["lesson_id"]}
        if method == "GET" and path.endswith("/lessons"):
            return {"lessons": list(lessons.values())}
        if method in ("PATCH", "POST", "PUT") and "/lessons/" in path and path.endswith("/status"):
            assert payload is not None
            lesson_id = path.split("/lessons/", 1)[1].split("/status", 1)[0]
            if lesson_id not in lessons:
                raise KeyError(lesson_id)
            rec = dict(lessons[lesson_id])
            rec["review_status"] = payload.get("review_status") or payload["status"]
            rec["reviewed_by"] = payload["reviewed_by"]
            rec["promotion_record_id"] = payload.get("promotion_record_id")
            lessons[lesson_id] = rec
            return {"lesson": rec}
        if method == "POST" and path.endswith("/query"):
            return {"facts": list(facts.values()), "lessons": list(lessons.values())}
        raise AssertionError(f"Unexpected REST call: {method} {url}")

    return handler


def _make_mcp_mock() -> Any:
    """In-memory mock for CondensateCore and GenericEndpoint MCP JSON-RPC calls."""
    facts: dict[str, dict[str, Any]] = {}
    lessons: dict[str, dict[str, Any]] = {}

    def handler(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if tool_name.endswith("health"):
            return {"healthy": True}
        if tool_name.endswith("record_fact"):
            fact = arguments.get("fact", arguments)
            facts[fact["fact_id"]] = fact
            return {"fact_id": fact["fact_id"]}
        if tool_name.endswith("record_lesson"):
            lesson = arguments.get("lesson", arguments)
            lessons[lesson["lesson_id"]] = lesson
            return {"lesson_id": lesson["lesson_id"]}
        if tool_name.endswith("update_lesson_status"):
            lesson_id = arguments["lesson_id"]
            if lesson_id not in lessons:
                raise KeyError(lesson_id)
            rec = dict(lessons[lesson_id])
            rec["review_status"] = arguments.get("review_status") or arguments["status"]
            rec["reviewed_by"] = arguments["reviewed_by"]
            rec["promotion_record_id"] = arguments.get("promotion_record_id")
            lessons[lesson_id] = rec
            return {"lesson": rec}
        if tool_name.endswith("list_facts"):
            return {"facts": list(facts.values())}
        if tool_name.endswith("list_lessons"):
            return {"lessons": list(lessons.values())}
        if tool_name.endswith("query"):
            return {"facts": list(facts.values()), "lessons": list(lessons.values())}
        raise AssertionError(f"Unexpected MCP tool: {tool_name}")

    return handler


def _make_mem0_mock() -> Any:
    items: list[dict[str, Any]] = []

    def handler(method: str, url: str, payload: dict[str, Any] | None, headers: dict[str, str]) -> dict[str, Any]:
        path = urlparse(url).path
        if method == "GET" and path.endswith("/health"):
            return {"status": "ok"}
        if method == "POST" and path.endswith("/v1/memories/"):
            assert payload is not None
            meta = dict(payload.get("metadata", {}))
            item_id = meta.get("fact_id") or meta.get("lesson_id") or f"mem-{len(items) + 1}"
            # Upsert by id
            items[:] = [i for i in items if i["id"] != item_id]
            items.append({"id": item_id, "memory": payload["messages"][0]["content"], "metadata": meta})
            return {"id": item_id}
        if method == "GET" and path.endswith("/v1/memories/"):
            return {"results": list(items)}
        if method == "POST" and path.endswith("/v1/memories/search/"):
            return {"results": list(items)}
        if method == "PUT" and "/v1/memories/" in path:
            assert payload is not None
            item_id = path.rstrip("/").split("/")[-1]
            for item in items:
                if item["id"] == item_id:
                    item["metadata"].update(payload.get("metadata", {}))
                    return item
            raise KeyError(item_id)
        raise AssertionError(f"Unexpected Mem0 call: {method} {url}")

    return handler


def _make_graphiti_mock() -> Any:
    episodes: dict[str, dict[str, Any]] = {}

    def handler(method: str, url: str, payload: dict[str, Any] | None, headers: dict[str, str]) -> dict[str, Any]:
        path = urlparse(url).path
        if method == "GET" and path.endswith("/healthcheck"):
            return {"status": "ok"}
        if method == "POST" and path.endswith("/episodes"):
            assert payload is not None
            name = payload["name"]
            episodes[name] = dict(payload)
            return {"id": name}
        if method == "GET" and path.endswith("/episodes"):
            return {"episodes": list(episodes.values())}
        if method == "POST" and path.endswith("/search"):
            return {"episodes": list(episodes.values())}
        raise AssertionError(f"Unexpected Graphiti call: {method} {url}")

    return handler


def _build_provider(kind: str, tmp_path: Any) -> MemoryProviderProtocol:
    if kind == "null":
        return create_memory_provider(MemoryProviderConfig(provider=MemoryProviderKind.NULL))
    if kind == "local":
        return create_memory_provider(
            MemoryProviderConfig(
                provider=MemoryProviderKind.LOCAL,
                local_path=str(tmp_path / "local_memory.json"),
            )
        )
    if kind == "condensate_core_http":
        return create_memory_provider(
            MemoryProviderConfig(
                provider=MemoryProviderKind.CONDENSATE_CORE,
                transport=MemoryTransport.HTTP,
                endpoint_url="http://127.0.0.1:8000",
            ),
            http_transport=_make_rest_mock(),
        )
    if kind == "condensate_core_mcp":
        return create_memory_provider(
            MemoryProviderConfig(
                provider=MemoryProviderKind.CONDENSATE_CORE,
                transport=MemoryTransport.MCP,
                mcp_endpoint="http://127.0.0.1:8765/mcp",
            ),
            mcp_transport=_make_mcp_mock(),
        )
    if kind == "generic_http":
        return create_memory_provider(
            MemoryProviderConfig(
                provider=MemoryProviderKind.CUSTOM_HTTP,
                transport=MemoryTransport.HTTP,
                endpoint_url="http://127.0.0.1:8090",
            ),
            http_transport=_make_rest_mock(),
        )
    if kind == "generic_mcp":
        return create_memory_provider(
            MemoryProviderConfig(
                provider=MemoryProviderKind.CUSTOM_MCP,
                transport=MemoryTransport.MCP,
                mcp_endpoint="http://127.0.0.1:8091/mcp",
            ),
            mcp_transport=_make_mcp_mock(),
        )
    if kind == "mem0":
        return create_memory_provider(
            MemoryProviderConfig(
                provider=MemoryProviderKind.MEM0,
                transport=MemoryTransport.HTTP,
                endpoint_url="http://127.0.0.1:8010",
            ),
            http_transport=_make_mem0_mock(),
        )
    if kind == "graphiti":
        return create_memory_provider(
            MemoryProviderConfig(
                provider=MemoryProviderKind.GRAPHITI,
                transport=MemoryTransport.HTTP,
                endpoint_url="http://127.0.0.1:8020",
            ),
            http_transport=_make_graphiti_mock(),
        )
    raise ValueError(f"Unknown conformance provider: {kind}")


ALL_PROVIDERS = [
    "null",
    "local",
    "condensate_core_http",
    "condensate_core_mcp",
    "generic_http",
    "generic_mcp",
    "mem0",
    "graphiti",
]

STATEFUL_PROVIDERS = [p for p in ALL_PROVIDERS if p != "null"]


@pytest.mark.parametrize("provider_kind", ALL_PROVIDERS)
def test_conformance_protocol_capabilities_and_health(provider_kind: str, tmp_path) -> None:
    provider = _build_provider(provider_kind, tmp_path)
    assert isinstance(provider, MemoryProviderProtocol)

    caps = provider.capabilities()
    assert isinstance(caps, MemoryCapabilities)
    assert caps.interface_version == MEMORY_INTERFACE_VERSION
    assert caps.health_check is True

    health = provider.health()
    assert isinstance(health, ProviderHealth)
    assert health.healthy is True


@pytest.mark.parametrize("provider_kind", STATEFUL_PROVIDERS)
def test_conformance_fact_lesson_separation_and_approval_gate(provider_kind: str, tmp_path) -> None:
    provider = _build_provider(provider_kind, tmp_path)
    gateway = MemoryGateway(provider=provider, redactor=Redactor(), authority_guard=MemoryAuthorityGuard())

    # 1. Record an ObservedFact with PII email that must be redacted
    fact = _sample_fact(summary="Auth token expiry defect reported by alice@internal.local")
    fact_id = gateway.publish_fact(fact)
    assert fact_id == fact.fact_id

    # 2. Record a PROPOSED (unapproved) InferredLesson
    proposed = _sample_lesson("lesson-proposed", status=LessonReviewStatus.PROPOSED)
    assert gateway.publish_lesson(proposed) == "lesson-proposed"

    # 3. Query without include_unapproved_lessons -> approved_lessons MUST be empty
    ctx_before = gateway.retrieve_advisory_context(
        MemoryQuery(query_text="auth token", domain_tags=["auth"], include_unapproved_lessons=False)
    )
    assert ctx_before.advisory_only is True
    assert ctx_before.degraded is False
    assert len(ctx_before.facts) >= 1
    assert "alice@internal.local" not in ctx_before.facts[0].summary
    assert "[REDACTED]" in ctx_before.facts[0].summary
    assert ctx_before.approved_lessons == []
    assert ctx_before.advisory_unapproved_lessons == []

    # 4. Promote lesson with human reviewer -> now enters approved_lessons
    promoted = gateway.promote_lesson(
        "lesson-proposed",
        status=LessonReviewStatus.APPROVED,
        reviewed_by="guardian-alice",
        promotion_record_id="promo-1",
    )
    assert promoted.is_policy_eligible is True

    ctx_after = gateway.retrieve_advisory_context(
        MemoryQuery(query_text="auth token", domain_tags=["auth"], include_unapproved_lessons=False)
    )
    assert any(l.lesson_id == "lesson-proposed" and l.is_policy_eligible for l in ctx_after.approved_lessons)


def test_conformance_null_provider_allows_zero_memory_operation() -> None:
    gateway = MemoryGateway(provider=NullMemoryProvider())
    assert gateway.publish_fact(_sample_fact()) == "fact-conf-1"
    ctx = gateway.retrieve_advisory_context(MemoryQuery(query_text="auth"))
    assert ctx.advisory_only is True
    assert ctx.degraded is False
    assert ctx.facts == []
    assert ctx.approved_lessons == []


def test_conformance_authority_guard_blocks_override_from_any_provider() -> None:
    guard = MemoryAuthorityGuard()
    bad_fact = _sample_fact()
    bad_fact = bad_fact.model_copy(update={"metadata": {"approval_granted": True}})
    bad_ctx = AdvisoryContext(provider_name="rogue", facts=[bad_fact])
    with pytest.raises(MemoryAuthorityViolationError):
        guard.validate_inbound_context(bad_ctx)


@pytest.mark.parametrize(
    "kind,transport,kwargs",
    [
        (MemoryProviderKind.CONDENSATE_CORE, MemoryTransport.HTTP, {"endpoint_url": "http://127.0.0.1:8000"}),
        (MemoryProviderKind.CUSTOM_HTTP, MemoryTransport.HTTP, {"endpoint_url": "http://127.0.0.1:8090"}),
        (MemoryProviderKind.MEM0, MemoryTransport.HTTP, {"endpoint_url": "http://127.0.0.1:8010"}),
        (MemoryProviderKind.GRAPHITI, MemoryTransport.HTTP, {"endpoint_url": "http://127.0.0.1:8020"}),
    ],
)
def test_conformance_offline_graceful_degradation(kind, transport, kwargs) -> None:
    def failing_http(*_args, **_kwargs):
        raise ConnectionError("offline endpoint")

    provider = create_memory_provider(
        MemoryProviderConfig(provider=kind, transport=transport, fail_open_on_unavailable=True, **kwargs),
        http_transport=failing_http,
    )
    gateway = MemoryGateway(provider=provider, fail_open=True)
    ctx = gateway.retrieve_advisory_context(MemoryQuery(query_text="auth"))
    assert ctx.advisory_only is True
    assert ctx.degraded is True
    assert ctx.facts == []
    assert ctx.approved_lessons == []
