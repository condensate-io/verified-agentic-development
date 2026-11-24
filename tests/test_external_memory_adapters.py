from __future__ import annotations

import json
from typing import Any

import pytest

from vad.memory.capabilities import MemoryCapabilities, ProviderHealth
from vad.memory.config import (
    MemoryProviderConfig,
    MemoryProviderKind,
    MemoryTransport,
)
from vad.memory.contracts import (
    AdvisoryContext,
    FactType,
    InferredLesson,
    LessonReviewStatus,
    MemoryQuery,
    ObservedFact,
    ProposedRuleType,
)
from vad.memory.gateway import MemoryGateway
from vad.memory.provider import MemoryProviderProtocol
from vad.memory.providers.generic_endpoint import (
    GenericEndpointMemoryProvider,
    RemoteEndpointError,
)
from vad.memory.providers.graphiti import (
    ENTITY_TYPE_FACT,
    ENTITY_TYPE_LESSON,
    GraphitiMemoryProvider,
)
from vad.memory.providers.local import LocalMemoryProvider
from vad.memory.providers.mem0 import Mem0MemoryProvider
from vad.memory.providers.null import NullMemoryProvider
from vad.memory.providers.registry import (
    create_memory_provider,
    list_custom_providers,
    register_custom_provider,
    unregister_custom_provider,
)

VALID_DIGEST = "a" * 64


def _make_fact(
    fact_id: str = "fact-1",
    summary: str = "Verification failed on auth token expiry check",
    fact_type: FactType = FactType.VERIFICATION_FAILURE,
    domain_tags: list[str] | None = None,
) -> ObservedFact:
    return ObservedFact(
        fact_id=fact_id,
        fact_type=fact_type,
        run_id="run-42",
        eip_digest="b" * 64,
        source_evidence_digest=VALID_DIGEST,
        risk_tier="high",
        domain_tags=domain_tags or ["auth", "security"],
        summary=summary,
    )


def _make_lesson(
    lesson_id: str = "lesson-1",
    title: str = "Enforce token TTL check",
    recommendation: str = "Always add expiry proof obligation for auth changes",
    domain_tags: list[str] | None = None,
    review_status: LessonReviewStatus = LessonReviewStatus.PROPOSED,
    reviewed_by: str | None = None,
) -> InferredLesson:
    return InferredLesson(
        lesson_id=lesson_id,
        title=title,
        recommendation=recommendation,
        proposed_rule_type=ProposedRuleType.PROOF_OBLIGATION,
        derived_from_fact_ids=["fact-1"],
        domain_tags=domain_tags or ["auth"],
        confidence=0.88,
        review_status=review_status,
        reviewed_by=reviewed_by,
    )


def test_generic_endpoint_http_route_overrides_separation_and_auth(monkeypatch) -> None:
    monkeypatch.setenv("VAD_TEST_MEMORY_TOKEN", "test-secret-ref-value")

    calls: list[tuple[str, str, dict[str, Any] | None, dict[str, str]]] = []
    stored_facts: dict[str, dict[str, Any]] = {}
    stored_lessons: dict[str, dict[str, Any]] = {}

    def mock_http(
        method: str,
        url: str,
        payload: dict[str, Any] | None,
        headers: dict[str, str],
        timeout: float,
    ) -> dict[str, Any]:
        calls.append((method, url, payload, headers))
        base_path = url.split("?", 1)[0]
        if method == "GET" and base_path == "http://127.0.0.1:8090/v2/healthz":
            return {"healthy": True, "detail": "custom endpoint ready"}
        if method == "POST" and base_path == "http://127.0.0.1:8090/v2/facts":
            assert payload is not None
            assert payload["record_kind"] == "observed_fact"
            fact_data = payload["fact"]
            stored_facts[fact_data["fact_id"]] = fact_data
            return {"fact_id": fact_data["fact_id"]}
        if method == "GET" and base_path == "http://127.0.0.1:8090/v2/facts":
            return {"facts": list(stored_facts.values())}
        if method == "POST" and base_path == "http://127.0.0.1:8090/v2/lessons":
            assert payload is not None
            assert payload["record_kind"] == "inferred_lesson"
            lesson_data = payload["lesson"]
            stored_lessons[lesson_data["lesson_id"]] = lesson_data
            return {"lesson_id": lesson_data["lesson_id"]}
        if method == "GET" and base_path == "http://127.0.0.1:8090/v2/lessons":
            return {"lessons": list(stored_lessons.values())}
        if method == "PATCH" and base_path == "http://127.0.0.1:8090/v2/lessons/l-approved/review":
            assert payload is not None
            lesson = stored_lessons.get("l-approved")
            if lesson is None:
                raise RemoteEndpointError("not found", status_code=404)
            lesson["review_status"] = payload["review_status"]
            lesson["reviewed_by"] = payload["reviewed_by"]
            lesson["promotion_record_id"] = payload["promotion_record_id"]
            return {"lesson": lesson}
        if method == "POST" and base_path == "http://127.0.0.1:8090/v2/search":
            # Simulate a buggy/untrusted remote endpoint trying to mix unapproved lesson into approved_lessons
            return {
                "facts": list(stored_facts.values()),
                "approved_lessons": list(stored_lessons.values()),
                "advisory_unapproved_lessons": [],
            }
        raise AssertionError(f"Unexpected HTTP call: {method} {url}")

    cfg = MemoryProviderConfig(
        provider=MemoryProviderKind.CUSTOM_HTTP,
        transport=MemoryTransport.HTTP,
        endpoint_url="http://127.0.0.1:8090",
        project_id="proj-alpha",
        auth_secret_ref="env:VAD_TEST_MEMORY_TOKEN",
        extra_endpoints={
            "health": "/v2/healthz",
            "facts": "/v2/facts",
            "lessons": "/v2/lessons",
            "lesson_status": "/v2/lessons/{lesson_id}/review",
            "query": "/v2/search",
        },
        options={"semantic_search": True},
    )
    provider = GenericEndpointMemoryProvider(cfg, http_transport=mock_http)
    assert isinstance(provider, MemoryProviderProtocol)

    caps = provider.capabilities()
    assert caps.semantic_search is True
    assert caps.supported_transports == ["http"]

    health = provider.health()
    assert health.healthy is True
    assert health.detail == "custom endpoint ready"
    assert calls[0][3]["Authorization"] == "Bearer test-secret-ref-value"
    assert calls[0][3]["X-VAD-Project-Id"] == "proj-alpha"

    fact = _make_fact(fact_id="f-1")
    unapproved = _make_lesson(lesson_id="l-proposed", review_status=LessonReviewStatus.PROPOSED)
    to_approve = _make_lesson(lesson_id="l-approved", review_status=LessonReviewStatus.PROPOSED)

    assert provider.record_fact(fact) == "f-1"
    assert provider.record_lesson(unapproved) == "l-proposed"
    assert provider.record_lesson(to_approve) == "l-approved"

    promoted = provider.update_lesson_status(
        "l-approved",
        LessonReviewStatus.APPROVED,
        reviewed_by="sec-lead",
        promotion_record_id="promo-9",
    )
    assert promoted.is_policy_eligible is True
    assert promoted.promotion_record_id == "promo-9"

    # Unapproved lesson must NEVER stay inside approved_lessons
    ctx_approved_only = provider.query(
        MemoryQuery(query_text="auth", include_unapproved_lessons=False)
    )
    assert [f.fact_id for f in ctx_approved_only.facts] == ["f-1"]
    assert [l.lesson_id for l in ctx_approved_only.approved_lessons] == ["l-approved"]
    assert ctx_approved_only.advisory_unapproved_lessons == []

    ctx_with_unapproved = provider.query(
        MemoryQuery(query_text="auth", include_unapproved_lessons=True)
    )
    assert [l.lesson_id for l in ctx_with_unapproved.approved_lessons] == ["l-approved"]
    assert [l.lesson_id for l in ctx_with_unapproved.advisory_unapproved_lessons] == [
        "l-proposed"
    ]

    assert len(provider.list_facts()) == 1
    assert [l.lesson_id for l in provider.list_lessons(status=LessonReviewStatus.APPROVED)] == [
        "l-approved"
    ]


def test_generic_endpoint_mcp_and_dual_transport_fallback() -> None:
    mcp_calls: list[tuple[str, dict[str, Any]]] = []
    facts_db: dict[str, dict[str, Any]] = {}
    lessons_db: dict[str, dict[str, Any]] = {}

    def failing_http(
        method: str,
        url: str,
        payload: dict[str, Any] | None,
        headers: dict[str, str],
        timeout: float,
    ) -> dict[str, Any]:
        raise RemoteEndpointError("primary http offline")

    def mock_mcp(
        tool_name: str,
        arguments: dict[str, Any],
        headers: dict[str, str],
        timeout: float,
    ) -> dict[str, Any]:
        mcp_calls.append((tool_name, arguments))
        if tool_name == "memory_health":
            return {"healthy": True, "detail": "mcp healthy"}
        if tool_name == "custom_fact_tool":
            fact_data = arguments["fact"]
            facts_db[fact_data["fact_id"]] = fact_data
            return {"fact_id": fact_data["fact_id"]}
        if tool_name == "memory_record_lesson":
            lesson_data = arguments["lesson"]
            lessons_db[lesson_data["lesson_id"]] = lesson_data
            return {"lesson_id": lesson_data["lesson_id"]}
        if tool_name == "memory_query":
            return {
                "facts": list(facts_db.values()),
                "lessons": list(lessons_db.values()),
            }
        raise AssertionError(f"Unexpected MCP tool: {tool_name}")

    # Pure CUSTOM_MCP with tool override
    mcp_cfg = MemoryProviderConfig(
        provider=MemoryProviderKind.CUSTOM_MCP,
        transport=MemoryTransport.MCP,
        mcp_endpoint="http://127.0.0.1:8765/mcp",
        extra_endpoints={"facts": "custom_fact_tool"},
    )
    mcp_provider = GenericEndpointMemoryProvider(mcp_cfg, mcp_transport=mock_mcp)
    assert mcp_provider.capabilities().supported_transports == ["mcp"]
    assert mcp_provider.health().healthy is True
    assert mcp_provider.record_fact(_make_fact("f-mcp")) == "f-mcp"
    assert mcp_calls[-1][0] == "custom_fact_tool"

    # BOTH transport falling back from failing HTTP to MCP
    both_cfg = MemoryProviderConfig(
        provider=MemoryProviderKind.CUSTOM_HTTP,
        transport=MemoryTransport.BOTH,
        endpoint_url="http://127.0.0.1:8090",
        mcp_endpoint="http://127.0.0.1:8765/mcp",
    )
    both_provider = GenericEndpointMemoryProvider(
        both_cfg,
        http_transport=failing_http,
        mcp_transport=mock_mcp,
    )
    assert both_provider.capabilities().supported_transports == ["http", "mcp"]
    assert both_provider.health().detail == "mcp healthy"
    assert both_provider.record_lesson(_make_lesson("l-mcp")) == "l-mcp"
    ctx = both_provider.query(MemoryQuery(query_text="auth", include_unapproved_lessons=True))
    assert [f.fact_id for f in ctx.facts] == ["f-mcp"]
    assert [l.lesson_id for l in ctx.advisory_unapproved_lessons] == ["l-mcp"]


def test_generic_endpoint_offline_degradation() -> None:
    def offline_http(*args: Any) -> dict[str, Any]:
        raise RemoteEndpointError("connection refused")

    fail_open_cfg = MemoryProviderConfig(
        provider=MemoryProviderKind.CUSTOM_HTTP,
        transport=MemoryTransport.HTTP,
        endpoint_url="http://127.0.0.1:8090",
        fail_open_on_unavailable=True,
    )
    degraded_provider = GenericEndpointMemoryProvider(fail_open_cfg, http_transport=offline_http)
    assert degraded_provider.health().healthy is False
    ctx = degraded_provider.query(MemoryQuery(query_text="auth"))
    assert ctx.degraded is True
    assert "connection refused" in (ctx.degraded_reason or "")
    assert ctx.facts == []
    assert ctx.approved_lessons == []
    assert degraded_provider.list_facts() == []
    assert degraded_provider.list_lessons() == []

    fail_closed_cfg = MemoryProviderConfig(
        provider=MemoryProviderKind.CUSTOM_HTTP,
        transport=MemoryTransport.HTTP,
        endpoint_url="http://127.0.0.1:8090",
        fail_open_on_unavailable=False,
    )
    strict_provider = GenericEndpointMemoryProvider(fail_closed_cfg, http_transport=offline_http)
    with pytest.raises(RemoteEndpointError):
        strict_provider.query(MemoryQuery(query_text="auth"))
    with pytest.raises(RemoteEndpointError):
        strict_provider.health()


def test_mem0_provider_mapping_separation_capabilities_and_degradation() -> None:
    memories: dict[str, dict[str, Any]] = {}
    counter = {"seq": 0}

    def mock_mem0_http(
        method: str,
        url: str,
        payload: dict[str, Any] | None,
        headers: dict[str, str],
        timeout: float,
    ) -> dict[str, Any]:
        base_path = url.split("?", 1)[0]
        if method == "GET" and base_path == "http://127.0.0.1:8010/health":
            return {"status": "ok"}
        if method == "POST" and base_path == "http://127.0.0.1:8010/v1/memories/":
            assert payload is not None
            counter["seq"] += 1
            mem_id = f"mem-{counter['seq']}"
            memories[mem_id] = {
                "id": mem_id,
                "memory": payload["messages"][0]["content"],
                "metadata": payload["metadata"],
            }
            return {"results": [memories[mem_id]]}
        if method == "GET" and base_path == "http://127.0.0.1:8010/v1/memories/":
            # Include an untagged non-VAD memory to verify it never blurs into facts or lessons
            extra = {
                "id": "mem-noise",
                "memory": "User likes dark mode",
                "metadata": {"source": "chat"},
            }
            return {"results": [*memories.values(), extra]}
        if method == "PUT" and base_path.startswith("http://127.0.0.1:8010/v1/memories/"):
            assert payload is not None
            mem_id = base_path.rstrip("/").rsplit("/", 1)[-1]
            if mem_id not in memories:
                raise RemoteEndpointError("not found", status_code=404)
            memories[mem_id]["memory"] = payload["text"]
            memories[mem_id]["metadata"] = payload["metadata"]
            return memories[mem_id]
        if method == "POST" and base_path == "http://127.0.0.1:8010/v1/memories/search/":
            return {"results": list(memories.values())}
        raise AssertionError(f"Unexpected Mem0 HTTP call: {method} {url}")

    cfg = MemoryProviderConfig(
        provider=MemoryProviderKind.MEM0,
        transport=MemoryTransport.HTTP,
        endpoint_url="http://127.0.0.1:8010",
        project_id="vad-mem0-test",
    )
    provider = Mem0MemoryProvider(cfg, http_transport=mock_mem0_http)
    assert isinstance(provider, MemoryProviderProtocol)
    assert provider.name == "mem0"

    caps = provider.capabilities()
    assert caps.semantic_search is True
    assert caps.causal_graph is False
    assert caps.contradiction_resolution is False
    assert caps.supported_transports == ["http"]
    assert provider.health().healthy is True

    fact = _make_fact("fact-m1", fact_type=FactType.INCIDENT)
    lesson_unapproved = _make_lesson("lesson-m1", review_status=LessonReviewStatus.PROPOSED)
    lesson_approved = _make_lesson("lesson-m2", review_status=LessonReviewStatus.PROPOSED)

    provider.record_fact(fact)
    provider.record_lesson(lesson_unapproved)
    provider.record_lesson(lesson_approved)

    # Verify record_kind tags on raw Mem0 payloads
    stored_kinds = {item["metadata"]["record_kind"] for item in memories.values()}
    assert stored_kinds == {"observed_fact", "inferred_lesson"}

    promoted = provider.update_lesson_status(
        "lesson-m2",
        LessonReviewStatus.APPROVED,
        reviewed_by="alice",
        promotion_record_id="promo-mem0",
    )
    assert promoted.is_policy_eligible is True
    assert promoted.reviewed_by == "alice"

    ctx = provider.query(
        MemoryQuery(
            query_text="auth",
            fact_types=[FactType.INCIDENT],
            include_unapproved_lessons=False,
        )
    )
    assert [f.fact_id for f in ctx.facts] == ["fact-m1"]
    assert [l.lesson_id for l in ctx.approved_lessons] == ["lesson-m2"]
    assert ctx.advisory_unapproved_lessons == []

    ctx_all = provider.query(MemoryQuery(query_text="auth", include_unapproved_lessons=True))
    assert [l.lesson_id for l in ctx_all.advisory_unapproved_lessons] == ["lesson-m1"]
    assert len(provider.list_facts()) == 1
    assert len(provider.list_lessons(status=LessonReviewStatus.APPROVED)) == 1

    with pytest.raises(KeyError):
        provider.update_lesson_status("nonexistent", LessonReviewStatus.REJECTED, "alice")

    # Offline degradation
    def offline(*args: Any) -> dict[str, Any]:
        raise RemoteEndpointError("mem0 unreachable")

    degraded = Mem0MemoryProvider(cfg, http_transport=offline)
    assert degraded.health().healthy is False
    degraded_ctx = degraded.query(MemoryQuery(query_text="auth"))
    assert degraded_ctx.degraded is True
    assert "mem0 unreachable" in (degraded_ctx.degraded_reason or "")


def test_graphiti_provider_episodes_entity_types_capabilities_and_degradation() -> None:
    episodes: list[dict[str, Any]] = []

    def mock_graphiti_http(
        method: str,
        url: str,
        payload: dict[str, Any] | None,
        headers: dict[str, str],
        timeout: float,
    ) -> dict[str, Any]:
        base_path = url.split("?", 1)[0]
        if method == "GET" and base_path == "http://localhost:8000/healthcheck":
            return {"status": "healthy"}
        if method == "POST" and base_path == "http://localhost:8000/episodes":
            assert payload is not None
            episodes.append(payload)
            return {"episode_id": f"ep-{len(episodes)}"}
        if method == "GET" and base_path == "http://localhost:8000/episodes":
            return {"episodes": list(episodes)}
        if method == "POST" and base_path == "http://localhost:8000/search":
            # Include an unrelated graph node with unknown entity_type
            noise_node = {
                "entity_type": "generic_entity",
                "name": "unrelated-node",
            }
            return {"episodes": [*episodes, noise_node]}
        raise AssertionError(f"Unexpected Graphiti HTTP call: {method} {url}")

    cfg = MemoryProviderConfig(
        provider=MemoryProviderKind.GRAPHITI,
        transport=MemoryTransport.HTTP,
        endpoint_url="http://localhost:8000",
        project_id="vad-graphiti-test",
    )
    provider = GraphitiMemoryProvider(cfg, http_transport=mock_graphiti_http)
    assert isinstance(provider, MemoryProviderProtocol)
    assert provider.name == "graphiti"

    caps = provider.capabilities()
    assert caps.semantic_search is True
    assert caps.causal_graph is True
    assert caps.provenance_traversal is True
    assert caps.supported_transports == ["http", "mcp"]
    assert provider.health().healthy is True

    fact = _make_fact("fact-g1", fact_type=FactType.VERIFIED_DELIVERY)
    lesson_1 = _make_lesson("lesson-g1", review_status=LessonReviewStatus.PROPOSED)
    lesson_2 = _make_lesson("lesson-g2", review_status=LessonReviewStatus.PROPOSED)

    provider.record_fact(fact)
    provider.record_lesson(lesson_1)
    provider.record_lesson(lesson_2)

    assert episodes[0]["entity_type"] == ENTITY_TYPE_FACT
    assert episodes[1]["entity_type"] == ENTITY_TYPE_LESSON
    assert json.loads(episodes[0]["episode_body"])["entity_type"] == ENTITY_TYPE_FACT

    promoted = provider.update_lesson_status(
        "lesson-g2",
        LessonReviewStatus.APPROVED,
        reviewed_by="bob",
        promotion_record_id="promo-g2",
    )
    assert promoted.is_policy_eligible is True

    ctx = provider.query(MemoryQuery(query_text="auth", include_unapproved_lessons=False))
    assert [f.fact_id for f in ctx.facts] == ["fact-g1"]
    assert [l.lesson_id for l in ctx.approved_lessons] == ["lesson-g2"]
    assert ctx.advisory_unapproved_lessons == []

    ctx_inc = provider.query(MemoryQuery(query_text="auth", include_unapproved_lessons=True))
    assert [l.lesson_id for l in ctx_inc.approved_lessons] == ["lesson-g2"]
    assert [l.lesson_id for l in ctx_inc.advisory_unapproved_lessons] == ["lesson-g1"]

    assert [f.fact_id for f in provider.list_facts()] == ["fact-g1"]
    assert [l.lesson_id for l in provider.list_lessons(status=LessonReviewStatus.APPROVED)] == [
        "lesson-g2"
    ]

    # Offline degradation
    def offline(*args: Any) -> dict[str, Any]:
        raise RemoteEndpointError("graphiti unreachable")

    degraded = GraphitiMemoryProvider(cfg, http_transport=offline)
    assert degraded.health().healthy is False
    degraded_ctx = degraded.query(MemoryQuery(query_text="auth"))
    assert degraded_ctx.degraded is True
    assert "graphiti unreachable" in (degraded_ctx.degraded_reason or "")


def test_registry_create_memory_provider_and_custom_plugin(tmp_path) -> None:
    assert isinstance(
        create_memory_provider(MemoryProviderConfig(provider=MemoryProviderKind.NULL)),
        NullMemoryProvider,
    )
    local_provider = create_memory_provider(
        MemoryProviderConfig(
            provider=MemoryProviderKind.LOCAL,
            local_path=str(tmp_path / "mem.json"),
        )
    )
    assert isinstance(local_provider, LocalMemoryProvider)

    dummy_http = lambda *args: {}
    dummy_mcp = lambda *args: {}

    mem0_provider = create_memory_provider(
        MemoryProviderConfig(
            provider=MemoryProviderKind.MEM0,
            transport=MemoryTransport.HTTP,
            endpoint_url="http://127.0.0.1:8010",
        ),
        http_transport=dummy_http,
    )
    assert isinstance(mem0_provider, Mem0MemoryProvider)

    graphiti_provider = create_memory_provider(
        MemoryProviderConfig(
            provider=MemoryProviderKind.GRAPHITI,
            transport=MemoryTransport.HTTP,
            endpoint_url="http://localhost:8000",
        ),
        http_transport=dummy_http,
    )
    assert isinstance(graphiti_provider, GraphitiMemoryProvider)

    custom_http_provider = create_memory_provider(
        MemoryProviderConfig(
            provider=MemoryProviderKind.CUSTOM_HTTP,
            transport=MemoryTransport.HTTP,
            endpoint_url="http://127.0.0.1:8090",
        ),
        http_transport=dummy_http,
    )
    assert isinstance(custom_http_provider, GenericEndpointMemoryProvider)

    custom_mcp_provider = create_memory_provider(
        MemoryProviderConfig(
            provider=MemoryProviderKind.CUSTOM_MCP,
            transport=MemoryTransport.MCP,
            mcp_endpoint="http://127.0.0.1:8765/mcp",
        ),
        mcp_transport=dummy_mcp,
    )
    assert isinstance(custom_mcp_provider, GenericEndpointMemoryProvider)

    # Custom in-process plugin registration
    class PluginMemoryProvider(NullMemoryProvider):
        name = "custom_plugin_demo"

        def __init__(self, config: MemoryProviderConfig) -> None:
            self.config = config

        def capabilities(self) -> MemoryCapabilities:
            return MemoryCapabilities(supported_transports=["in_process"])

        def health(self) -> ProviderHealth:
            return ProviderHealth(healthy=True, provider_name=self.name, detail="plugin ok")

    plugin_name = "demo-in-process-plugin"
    try:
        register_custom_provider(plugin_name, lambda cfg: PluginMemoryProvider(cfg))
        assert plugin_name in list_custom_providers()

        plugin_provider = create_memory_provider(
            MemoryProviderConfig(
                provider=MemoryProviderKind.NULL,
                options={"custom_provider": plugin_name},
            )
        )
        assert isinstance(plugin_provider, PluginMemoryProvider)
        assert isinstance(plugin_provider, MemoryProviderProtocol)
        assert plugin_provider.health().detail == "plugin ok"
    finally:
        unregister_custom_provider(plugin_name)


def test_external_adapter_with_memory_gateway_redaction_and_guard() -> None:
    recorded_payloads: list[dict[str, Any]] = []

    def mock_http(
        method: str,
        url: str,
        payload: dict[str, Any] | None,
        headers: dict[str, str],
        timeout: float,
    ) -> dict[str, Any]:
        if method == "POST" and url.endswith("/v1/memories/"):
            assert payload is not None
            recorded_payloads.append(payload)
            return {"id": "mem-gw-1"}
        if method == "POST" and url.endswith("/v1/memories/search/"):
            return {
                "results": [
                    {
                        "id": "mem-gw-1",
                        "memory": recorded_payloads[0]["messages"][0]["content"],
                        "metadata": recorded_payloads[0]["metadata"],
                    }
                ]
            }
        return {}

    cfg = MemoryProviderConfig(
        provider=MemoryProviderKind.MEM0,
        transport=MemoryTransport.HTTP,
        endpoint_url="http://127.0.0.1:8010",
    )
    provider = create_memory_provider(cfg, http_transport=mock_http)
    gw = MemoryGateway(provider=provider)

    fact = _make_fact("f-gw", summary="Failure reported by alice@example.com")
    assert gw.publish_fact(fact) == "f-gw"
    assert "alice@example.com" not in recorded_payloads[0]["messages"][0]["content"]
    assert "[REDACTED]" in recorded_payloads[0]["messages"][0]["content"]

    ctx = gw.retrieve_advisory_context(MemoryQuery(query_text="Failure"))
    assert ctx.advisory_only is True
    assert len(ctx.facts) == 1
    assert "[REDACTED]" in ctx.facts[0].summary
