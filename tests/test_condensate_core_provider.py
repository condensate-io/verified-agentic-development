from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest

from vad.memory.config import (
    MemoryProviderConfig,
    MemoryProviderKind,
    MemoryTransport,
)
from vad.memory.contracts import (
    FactType,
    InferredLesson,
    LessonReviewStatus,
    MemoryQuery,
    ObservedFact,
    ProposedRuleType,
)
from vad.memory.gateway import MemoryGateway
from vad.memory.provider import MemoryProviderProtocol
from vad.memory.providers.condensate_core import CondensateCoreProvider

VALID_DIGEST = "a" * 64


def _make_fact(
    fact_id: str = "fact-1",
    summary: str = "Verification failed due to missing proof",
    fact_type: FactType = FactType.VERIFICATION_FAILURE,
    domain_tags: list[str] | None = None,
) -> ObservedFact:
    return ObservedFact(
        fact_id=fact_id,
        fact_type=fact_type,
        run_id="run-1",
        eip_digest="b" * 64,
        source_evidence_digest=VALID_DIGEST,
        risk_tier="high",
        domain_tags=domain_tags or ["auth", "proof"],
        summary=summary,
    )


def _make_lesson(
    lesson_id: str = "lesson-1",
    title: str = "Require auth proof obligation",
    recommendation: str = "Add unit proof for token expiry checks",
    domain_tags: list[str] | None = None,
    review_status: LessonReviewStatus = LessonReviewStatus.PROPOSED,
    reviewed_by: str | None = None,
    promotion_record_id: str | None = None,
) -> InferredLesson:
    return InferredLesson(
        lesson_id=lesson_id,
        title=title,
        recommendation=recommendation,
        proposed_rule_type=ProposedRuleType.PROOF_OBLIGATION,
        derived_from_fact_ids=["fact-1"],
        domain_tags=domain_tags or ["auth"],
        review_status=review_status,
        reviewed_by=reviewed_by,
        promotion_record_id=promotion_record_id,
    )


class _InMemoryCondensateBackend:
    """Deterministic in-memory mock backend serving both HTTP REST and MCP JSON-RPC."""

    def __init__(self) -> None:
        self.facts: dict[str, dict[str, Any]] = {}
        self.lessons: dict[str, dict[str, Any]] = {}
        self.http_calls: list[tuple[str, str, dict[str, Any] | None, dict[str, str]]] = []
        self.mcp_calls: list[tuple[str, dict[str, Any]]] = []

    def http_handler(
        self,
        method: str,
        url: str,
        payload: dict[str, Any] | None,
        headers: dict[str, str],
    ) -> dict[str, Any]:
        self.http_calls.append((method, url, payload, dict(headers)))
        parsed = urlparse(url)
        path = parsed.path
        qs = parse_qs(parsed.query)

        if method == "GET" and path.endswith("/health"):
            return {"healthy": True, "detail": "http backend ready"}

        if method == "POST" and path.endswith("/facts"):
            assert payload is not None
            fact_data = payload["fact"]
            self.facts[fact_data["fact_id"]] = fact_data
            return {"fact_id": fact_data["fact_id"]}

        if method == "GET" and path.endswith("/facts"):
            limit = int(qs.get("limit", ["50"])[0])
            return {"facts": list(self.facts.values())[:limit]}

        if method == "POST" and path.endswith("/lessons"):
            assert payload is not None
            lesson_data = payload["lesson"]
            self.lessons[lesson_data["lesson_id"]] = lesson_data
            return {"lesson_id": lesson_data["lesson_id"]}

        if method == "GET" and path.endswith("/lessons"):
            limit = int(qs.get("limit", ["50"])[0])
            status_filter = qs.get("status", [None])[0]
            items = list(self.lessons.values())
            if status_filter is not None:
                items = [item for item in items if item.get("review_status") == status_filter]
            return {"lessons": items[:limit]}

        if method == "POST" and path.endswith("/status"):
            assert payload is not None
            lesson_id = payload["lesson_id"]
            if lesson_id not in self.lessons:
                raise KeyError(f"Lesson not found: {lesson_id}")
            updated = dict(self.lessons[lesson_id])
            updated["review_status"] = payload["status"]
            updated["reviewed_by"] = payload["reviewed_by"]
            updated["reviewed_at"] = datetime.now(timezone.utc).isoformat()
            if payload.get("promotion_record_id") is not None:
                updated["promotion_record_id"] = payload["promotion_record_id"]
            self.lessons[lesson_id] = updated
            return {"lesson": updated}

        if method == "POST" and path.endswith("/query"):
            assert payload is not None
            query_data = payload["query"]
            return self._query_payload(query_data)

        raise RuntimeError(f"Unhandled mock HTTP route: {method} {url}")

    def mcp_handler(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        self.mcp_calls.append((tool_name, arguments))

        if tool_name == "condensate_health":
            return {
                "jsonrpc": "2.0",
                "id": "rpc-health",
                "result": {"healthy": True, "detail": "mcp backend ready"},
            }

        if tool_name == "condensate_record_fact":
            fact_data = arguments["fact"]
            self.facts[fact_data["fact_id"]] = fact_data
            return {
                "jsonrpc": "2.0",
                "id": "rpc-fact",
                "result": {"fact_id": fact_data["fact_id"]},
            }

        if tool_name == "condensate_record_lesson":
            lesson_data = arguments["lesson"]
            self.lessons[lesson_data["lesson_id"]] = lesson_data
            return {
                "jsonrpc": "2.0",
                "id": "rpc-lesson",
                "result": {"lesson_id": lesson_data["lesson_id"]},
            }

        if tool_name == "condensate_update_lesson_status":
            lesson_id = arguments["lesson_id"]
            if lesson_id not in self.lessons:
                return {
                    "jsonrpc": "2.0",
                    "id": "rpc-update",
                    "error": {"code": -32004, "message": f"Lesson not found: {lesson_id}"},
                }
            updated = dict(self.lessons[lesson_id])
            updated["review_status"] = arguments["status"]
            updated["reviewed_by"] = arguments["reviewed_by"]
            updated["reviewed_at"] = datetime.now(timezone.utc).isoformat()
            if arguments.get("promotion_record_id") is not None:
                updated["promotion_record_id"] = arguments["promotion_record_id"]
            self.lessons[lesson_id] = updated
            return {
                "jsonrpc": "2.0",
                "id": "rpc-update",
                "result": {"lesson": updated},
            }

        if tool_name == "condensate_query":
            return {
                "jsonrpc": "2.0",
                "id": "rpc-query",
                "result": self._query_payload(arguments["query"]),
            }

        if tool_name == "condensate_list_facts":
            limit = int(arguments.get("limit", 50))
            return {
                "jsonrpc": "2.0",
                "id": "rpc-list-facts",
                "result": {"facts": list(self.facts.values())[:limit]},
            }

        if tool_name == "condensate_list_lessons":
            limit = int(arguments.get("limit", 50))
            status_filter = arguments.get("status")
            items = list(self.lessons.values())
            if status_filter is not None:
                items = [item for item in items if item.get("review_status") == status_filter]
            return {
                "jsonrpc": "2.0",
                "id": "rpc-list-lessons",
                "result": {"lessons": items[:limit]},
            }

        raise RuntimeError(f"Unknown MCP tool: {tool_name}")

    def _query_payload(self, query_data: dict[str, Any]) -> dict[str, Any]:
        query_text = query_data.get("query_text", "").strip().lower()
        query_tags = {t.lower() for t in query_data.get("domain_tags", [])}
        fact_types = set(query_data.get("fact_types", []))

        matched_facts = []
        for fact in self.facts.values():
            if fact_types and fact["fact_type"] not in fact_types:
                continue
            text_ok = not query_text or query_text in fact["summary"].lower()
            tag_ok = not query_tags or bool(query_tags & {t.lower() for t in fact.get("domain_tags", [])})
            if text_ok or tag_ok:
                matched_facts.append(fact)

        matched_lessons = []
        for lesson in self.lessons.values():
            text_ok = (
                not query_text
                or query_text in lesson["title"].lower()
                or query_text in lesson["recommendation"].lower()
            )
            tag_ok = not query_tags or bool(query_tags & {t.lower() for t in lesson.get("domain_tags", [])})
            if text_ok or tag_ok:
                matched_lessons.append(lesson)

        return {"facts": matched_facts, "lessons": matched_lessons}


def test_condensate_core_protocol_and_capabilities() -> None:
    cfg = MemoryProviderConfig(
        provider=MemoryProviderKind.CONDENSATE_CORE,
        transport=MemoryTransport.HTTP,
        endpoint_url="http://127.0.0.1:8000",
    )
    provider = CondensateCoreProvider(config=cfg)

    assert isinstance(provider, MemoryProviderProtocol)
    assert provider.name == "condensate_core"

    caps = provider.capabilities()
    assert caps.fact_storage is True
    assert caps.lesson_storage is True
    assert caps.scoped_query is True
    assert caps.health_check is True
    assert caps.semantic_search is True
    assert caps.causal_graph is True
    assert caps.contradiction_resolution is True
    assert caps.context_consolidation is True
    assert caps.provenance_traversal is True
    assert caps.supported_transports == ["http", "mcp", "both"]


def test_condensate_core_http_transport_crud_query_and_route_overrides() -> None:
    backend = _InMemoryCondensateBackend()
    cfg = MemoryProviderConfig(
        provider=MemoryProviderKind.CONDENSATE_CORE,
        transport=MemoryTransport.HTTP,
        endpoint_url="http://127.0.0.1:8000",
        project_id="proj-alpha",
        extra_endpoints={"health": "/custom/v1/health"},
    )
    provider = CondensateCoreProvider(config=cfg, http_transport=backend.http_handler)

    health = provider.health()
    assert health.healthy is True
    assert health.provider_name == "condensate_core"
    assert backend.http_calls[-1][1] == "http://127.0.0.1:8000/custom/v1/health"

    fact_auth = _make_fact(
        fact_id="f-1",
        summary="Token validation failed",
        fact_type=FactType.VERIFICATION_FAILURE,
        domain_tags=["auth"],
    )
    fact_db = _make_fact(
        fact_id="f-2",
        summary="Migration passed",
        fact_type=FactType.VERIFIED_DELIVERY,
        domain_tags=["db"],
    )
    lesson_unapproved = _make_lesson(
        lesson_id="l-proposed",
        title="Token expiry check",
        recommendation="Validate token exp claim",
    )
    lesson_to_approve = _make_lesson(
        lesson_id="l-approved",
        title="Require token signature check",
        recommendation="Always verify JWT signature",
    )

    assert provider.record_fact(fact_auth) == "f-1"
    assert provider.record_fact(fact_db) == "f-2"
    assert provider.record_lesson(lesson_unapproved) == "l-proposed"
    assert provider.record_lesson(lesson_to_approve) == "l-approved"

    promoted = provider.update_lesson_status(
        "l-approved",
        LessonReviewStatus.APPROVED,
        reviewed_by="sec-lead",
        promotion_record_id="promo-1",
    )
    assert promoted.is_policy_eligible is True
    assert promoted.reviewed_by == "sec-lead"
    assert promoted.promotion_record_id == "promo-1"

    with pytest.raises(KeyError):
        provider.update_lesson_status("missing-lesson", LessonReviewStatus.REJECTED, "sec-lead")

    assert len(provider.list_facts(limit=10)) == 2
    assert len(provider.list_facts(limit=1)) == 1
    assert len(provider.list_lessons(limit=10)) == 2
    approved_only_list = provider.list_lessons(status=LessonReviewStatus.APPROVED, limit=10)
    assert [item.lesson_id for item in approved_only_list] == ["l-approved"]

    ctx_approved_only = provider.query(
        MemoryQuery(
            query_text="token",
            fact_types=[FactType.VERIFICATION_FAILURE],
            include_unapproved_lessons=False,
        )
    )
    assert ctx_approved_only.provider_name == "condensate_core"
    assert ctx_approved_only.advisory_only is True
    assert [f.fact_id for f in ctx_approved_only.facts] == ["f-1"]
    assert [l.lesson_id for l in ctx_approved_only.approved_lessons] == ["l-approved"]
    assert ctx_approved_only.advisory_unapproved_lessons == []

    ctx_with_unapproved = provider.query(
        MemoryQuery(
            query_text="token",
            include_unapproved_lessons=True,
        )
    )
    assert [l.lesson_id for l in ctx_with_unapproved.approved_lessons] == ["l-approved"]
    assert [l.lesson_id for l in ctx_with_unapproved.advisory_unapproved_lessons] == ["l-proposed"]


def test_condensate_core_strict_separation_blocks_smuggled_unapproved_lessons() -> None:
    def smuggle_http(method: str, url: str, payload: dict | None, headers: dict[str, str]) -> dict:
        return {
            "facts": [],
            "approved_lessons": [
                _make_lesson(
                    lesson_id="l-smuggled",
                    review_status=LessonReviewStatus.PROPOSED,
                    reviewed_by=None,
                ).model_dump(mode="json")
            ],
        }

    cfg = MemoryProviderConfig(
        provider=MemoryProviderKind.CONDENSATE_CORE,
        transport=MemoryTransport.HTTP,
        endpoint_url="http://localhost:8000",
    )
    provider = CondensateCoreProvider(config=cfg, http_transport=smuggle_http)

    ctx_no_unapproved = provider.query(
        MemoryQuery(query_text="auth", include_unapproved_lessons=False)
    )
    assert ctx_no_unapproved.approved_lessons == []
    assert ctx_no_unapproved.advisory_unapproved_lessons == []

    ctx_with_unapproved = provider.query(
        MemoryQuery(query_text="auth", include_unapproved_lessons=True)
    )
    assert ctx_with_unapproved.approved_lessons == []
    assert [l.lesson_id for l in ctx_with_unapproved.advisory_unapproved_lessons] == ["l-smuggled"]


def test_condensate_core_mcp_transport_json_rpc_lifecycle() -> None:
    backend = _InMemoryCondensateBackend()
    cfg = MemoryProviderConfig(
        provider=MemoryProviderKind.CONDENSATE_CORE,
        transport=MemoryTransport.MCP,
        mcp_endpoint="http://127.0.0.1:8765/mcp",
        project_id="proj-mcp",
    )
    provider = CondensateCoreProvider(config=cfg, mcp_transport=backend.mcp_handler)

    health = provider.health()
    assert health.healthy is True
    assert "mcp" in health.detail.lower()

    fact = _make_fact(fact_id="mcp-f1", summary="MCP fact summary")
    lesson = _make_lesson(lesson_id="mcp-l1", title="MCP lesson", recommendation="Check MCP")
    assert provider.record_fact(fact) == "mcp-f1"
    assert provider.record_lesson(lesson) == "mcp-l1"

    promoted = provider.update_lesson_status(
        "mcp-l1", LessonReviewStatus.APPROVED, reviewed_by="reviewer-1"
    )
    assert promoted.is_policy_eligible is True

    assert [f.fact_id for f in provider.list_facts()] == ["mcp-f1"]
    assert [l.lesson_id for l in provider.list_lessons(status=LessonReviewStatus.APPROVED)] == [
        "mcp-l1"
    ]

    ctx = provider.query(MemoryQuery(query_text="MCP"))
    assert [f.fact_id for f in ctx.facts] == ["mcp-f1"]
    assert [l.lesson_id for l in ctx.approved_lessons] == ["mcp-l1"]

    with pytest.raises(KeyError):
        provider.update_lesson_status("nonexistent", LessonReviewStatus.REJECTED, "reviewer-1")

    invoked_tools = [name for name, _ in backend.mcp_calls]
    assert invoked_tools == [
        "condensate_health",
        "condensate_record_fact",
        "condensate_record_lesson",
        "condensate_update_lesson_status",
        "condensate_list_facts",
        "condensate_list_lessons",
        "condensate_query",
        "condensate_update_lesson_status",
    ]


def test_condensate_core_both_transport_failover_http_to_mcp() -> None:
    backend = _InMemoryCondensateBackend()
    http_attempts: list[str] = []

    def failing_http(method: str, url: str, payload: dict | None, headers: dict[str, str]) -> dict:
        http_attempts.append(url)
        raise ConnectionError("HTTP endpoint unreachable")

    cfg = MemoryProviderConfig(
        provider=MemoryProviderKind.CONDENSATE_CORE,
        transport=MemoryTransport.BOTH,
        endpoint_url="http://127.0.0.1:8000",
        mcp_endpoint="stdio://condensate-mcp",
    )
    provider = CondensateCoreProvider(
        config=cfg,
        http_transport=failing_http,
        mcp_transport=backend.mcp_handler,
    )

    health = provider.health()
    assert health.healthy is True
    assert len(http_attempts) == 1
    assert backend.mcp_calls[0][0] == "condensate_health"

    assert provider.record_fact(_make_fact(fact_id="f-failover")) == "f-failover"
    assert provider.record_lesson(_make_lesson(lesson_id="l-failover")) == "l-failover"
    provider.update_lesson_status("l-failover", LessonReviewStatus.APPROVED, "alice")

    ctx = provider.query(MemoryQuery(query_text="auth"))
    assert ctx.degraded is False
    assert [f.fact_id for f in ctx.facts] == ["f-failover"]
    assert [l.lesson_id for l in ctx.approved_lessons] == ["l-failover"]
    assert len(http_attempts) == 5


def test_condensate_core_graceful_offline_degradation_and_fail_closed() -> None:
    def failing_http(method: str, url: str, payload: dict | None, headers: dict[str, str]) -> dict:
        raise ConnectionError("HTTP down")

    def failing_mcp(tool_name: str, arguments: dict[str, Any]) -> dict:
        raise RuntimeError("MCP process crashed")

    fail_open_cfg = MemoryProviderConfig(
        provider=MemoryProviderKind.CONDENSATE_CORE,
        transport=MemoryTransport.BOTH,
        endpoint_url="http://127.0.0.1:8000",
        mcp_endpoint="stdio://condensate-mcp",
        fail_open_on_unavailable=True,
    )
    provider = CondensateCoreProvider(
        config=fail_open_cfg,
        http_transport=failing_http,
        mcp_transport=failing_mcp,
    )

    health = provider.health()
    assert health.healthy is False
    assert health.provider_name == "condensate_core"
    assert "HTTP down" in health.detail
    assert "MCP process crashed" in health.detail

    ctx = provider.query(MemoryQuery(query_text="auth"))
    assert ctx.provider_name == "condensate_core"
    assert ctx.degraded is True
    assert ctx.advisory_only is True
    assert ctx.facts == []
    assert ctx.approved_lessons == []
    assert ctx.advisory_unapproved_lessons == []
    assert "HTTP down" in (ctx.degraded_reason or "")
    assert "MCP process crashed" in (ctx.degraded_reason or "")

    fail_closed_cfg = fail_open_cfg.model_copy(update={"fail_open_on_unavailable": False})
    strict_provider = CondensateCoreProvider(
        config=fail_closed_cfg,
        http_transport=failing_http,
        mcp_transport=failing_mcp,
    )
    with pytest.raises(RuntimeError, match="MCP process crashed"):
        strict_provider.query(MemoryQuery(query_text="auth"))


def test_condensate_core_auth_secret_ref_injection_without_leaking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret_value = "condensate-ephemeral-token-9988"
    monkeypatch.setenv("TEST_CONDENSATE_TOKEN", secret_value)

    captured_headers: list[dict[str, str]] = []

    def inspecting_http(
        method: str,
        url: str,
        payload: dict | None,
        headers: dict[str, str],
    ) -> dict[str, Any]:
        captured_headers.append(dict(headers))
        if url.endswith("/query"):
            raise ConnectionError(f"upstream rejected {secret_value}")
        return {"healthy": True, "detail": "authenticated"}

    cfg = MemoryProviderConfig(
        provider=MemoryProviderKind.CONDENSATE_CORE,
        transport=MemoryTransport.HTTP,
        endpoint_url="http://127.0.0.1:8000",
        auth_secret_ref="env:TEST_CONDENSATE_TOKEN",
    )
    provider = CondensateCoreProvider(config=cfg, http_transport=inspecting_http)

    # Raw secret must never be stored on provider or config
    assert secret_value not in repr(provider.__dict__)
    assert secret_value not in provider.config.model_dump_json()

    health = provider.health()
    assert health.healthy is True
    assert captured_headers[0]["Authorization"] == f"Bearer {secret_value}"
    assert secret_value not in health.model_dump_json()

    # Even if an upstream error echoes the token, degraded_reason must scrub it
    ctx = provider.query(MemoryQuery(query_text="auth"))
    assert ctx.degraded is True
    assert secret_value not in ctx.model_dump_json()
    assert "[REDACTED]" in (ctx.degraded_reason or "")


def test_condensate_core_integrates_with_memory_gateway() -> None:
    backend = _InMemoryCondensateBackend()
    cfg = MemoryProviderConfig(
        provider=MemoryProviderKind.CONDENSATE_CORE,
        transport=MemoryTransport.HTTP,
        endpoint_url="http://127.0.0.1:8000",
    )
    provider = CondensateCoreProvider(config=cfg, http_transport=backend.http_handler)
    gateway = MemoryGateway(provider=provider)

    assert gateway.capabilities().semantic_search is True
    assert gateway.health().healthy is True
    assert gateway.publish_fact(_make_fact(fact_id="gw-f1")) == "gw-f1"
    assert gateway.publish_lesson(_make_lesson(lesson_id="gw-l1")) == "gw-l1"

    ctx = gateway.retrieve_advisory_context(MemoryQuery(query_text="auth"))
    assert [f.fact_id for f in ctx.facts] == ["gw-f1"]
    assert ctx.approved_lessons == []
