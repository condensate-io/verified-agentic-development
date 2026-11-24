from __future__ import annotations

import inspect
import json
import os
import time
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

from vad.memory.capabilities import MemoryCapabilities, ProviderHealth
from vad.memory.config import MemoryProviderConfig, MemoryProviderKind, MemoryTransport
from vad.memory.contracts import (
    AdvisoryContext,
    InferredLesson,
    LessonReviewStatus,
    MemoryQuery,
    ObservedFact,
)

HttpTransport = Callable[
    [str, str, dict[str, Any] | None, dict[str, str], float],
    dict[str, Any],
]
McpTransport = Callable[
    [str, dict[str, Any], dict[str, str], float],
    dict[str, Any],
]

_DEFAULT_HTTP_ROUTES: dict[str, str] = {
    "health": "/health",
    "facts": "/facts",
    "lessons": "/lessons",
    "lesson_status": "/lessons/{lesson_id}/status",
    "query": "/query",
}

_DEFAULT_MCP_TOOLS: dict[str, str] = {
    "health": "memory_health",
    "record_fact": "memory_record_fact",
    "record_lesson": "memory_record_lesson",
    "lesson_status": "memory_update_lesson_status",
    "query": "memory_query",
    "list_facts": "memory_list_facts",
    "list_lessons": "memory_list_lessons",
}


class RemoteEndpointError(ConnectionError):
    """Raised when an external memory endpoint request fails."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


def resolve_auth_headers(config: MemoryProviderConfig) -> dict[str, str]:
    """Resolve auth_secret_ref into request headers without embedding raw secrets."""
    headers: dict[str, str] = {"X-VAD-Project-Id": config.project_id}
    ref = config.auth_secret_ref
    if not ref:
        return headers

    if ref.startswith("env:"):
        env_var = ref[4:].strip()
        token = os.environ.get(env_var, "").strip()
        if token:
            headers["Authorization"] = f"Bearer {token}"
    elif ref.startswith("file:"):
        secret_path = Path(ref[5:].strip())
        if secret_path.is_file():
            token = secret_path.read_text(encoding="utf-8").strip()
            if token:
                headers["Authorization"] = f"Bearer {token}"
    else:
        headers["X-VAD-Auth-Secret-Ref"] = ref

    return headers


def default_http_transport(
    method: str,
    url: str,
    payload: dict[str, Any] | None,
    headers: dict[str, str],
    timeout: float,
) -> dict[str, Any]:
    """Execute a JSON HTTP request using Python stdlib urllib."""
    scheme = urlsplit(url).scheme.lower()
    if scheme not in {"http", "https"}:
        raise RemoteEndpointError(f"Unsupported HTTP URL scheme for memory endpoint: {url}")

    req_headers = {
        "Accept": "application/json",
        **headers,
    }
    data: bytes | None = None
    if payload is not None:
        req_headers["Content-Type"] = "application/json"
        data = json.dumps(payload).encode("utf-8")

    request = Request(url=url, data=data, headers=req_headers, method=method.upper())
    try:
        with urlopen(request, timeout=timeout) as response:
            raw_bytes = response.read()
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace") if exc.fp else str(exc)
        raise RemoteEndpointError(
            f"HTTP {exc.code} from {url}: {detail}",
            status_code=exc.code,
        ) from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise RemoteEndpointError(f"HTTP transport error for {url}: {exc}") from exc

    if not raw_bytes:
        return {}
    try:
        decoded = json.loads(raw_bytes.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise RemoteEndpointError(f"Invalid JSON response from {url}: {exc}") from exc

    if isinstance(decoded, list):
        return {"results": decoded}
    if isinstance(decoded, dict):
        return decoded
    return {"result": decoded}


def default_mcp_transport(
    endpoint: str,
    tool_name: str,
    arguments: dict[str, Any],
    headers: dict[str, str],
    timeout: float,
) -> dict[str, Any]:
    """Execute an MCP JSON-RPC 2.0 tools/call request over HTTP(S)."""
    scheme = urlsplit(endpoint).scheme.lower()
    if scheme not in {"http", "https"}:
        raise RemoteEndpointError(
            f"MCP endpoint {endpoint!r} requires an HTTP(S) URL or injected mcp_transport"
        )

    rpc_payload = {
        "jsonrpc": "2.0",
        "id": f"vad-{tool_name}",
        "method": "tools/call",
        "params": {
            "name": tool_name,
            "arguments": arguments,
        },
    }
    rpc_response = default_http_transport("POST", endpoint, rpc_payload, headers, timeout)
    if "error" in rpc_response and rpc_response["error"] is not None:
        err = rpc_response["error"]
        message = err.get("message", str(err)) if isinstance(err, dict) else str(err)
        raise RemoteEndpointError(f"MCP error calling {tool_name}: {message}")

    result = rpc_response.get("result", rpc_response)
    if isinstance(result, dict):
        if isinstance(result.get("structuredContent"), dict):
            return result["structuredContent"]
        content = result.get("content")
        if isinstance(content, list) and content:
            first = content[0]
            if isinstance(first, dict) and isinstance(first.get("text"), str):
                try:
                    parsed = json.loads(first["text"])
                    if isinstance(parsed, dict):
                        return parsed
                    if isinstance(parsed, list):
                        return {"results": parsed}
                except json.JSONDecodeError:
                    return {"detail": first["text"]}
        return result
    return {"result": result}


def invoke_http_transport(
    transport: HttpTransport,
    method: str,
    url: str,
    payload: dict[str, Any] | None,
    headers: dict[str, str],
    timeout: float,
) -> dict[str, Any]:
    """Invoke an injected or default HTTP transport callable."""
    sig = inspect.signature(transport)
    params = list(sig.parameters.values())
    has_varargs = any(p.kind == inspect.Parameter.VAR_POSITIONAL for p in params)
    if has_varargs or len(params) >= 5:
        result = transport(method, url, payload, headers, timeout)
    elif len(params) == 4:
        result = transport(method, url, payload, headers)  # type: ignore[call-arg]
    else:
        result = transport(method, url, payload)  # type: ignore[call-arg]
    if isinstance(result, list):
        return {"results": result}
    return result if isinstance(result, dict) else {}


def invoke_mcp_transport(
    transport: McpTransport,
    tool_name: str,
    arguments: dict[str, Any],
    headers: dict[str, str],
    timeout: float,
) -> dict[str, Any]:
    """Invoke an injected MCP transport callable."""
    sig = inspect.signature(transport)
    params = list(sig.parameters.values())
    has_varargs = any(p.kind == inspect.Parameter.VAR_POSITIONAL for p in params)
    if has_varargs or len(params) >= 4:
        result = transport(tool_name, arguments, headers, timeout)
    else:
        result = transport(tool_name, arguments)  # type: ignore[call-arg]
    if isinstance(result, list):
        return {"results": result}
    return result if isinstance(result, dict) else {}


def build_endpoint_url(
    base_url: str | None,
    route_or_url: str,
    query_params: dict[str, Any] | None = None,
) -> str:
    """Resolve a route override or relative path against a base endpoint URL."""
    if route_or_url.startswith(("http://", "https://")):
        url = route_or_url
    else:
        if not base_url:
            raise RemoteEndpointError("No HTTP endpoint_url configured")
        url = f"{base_url.rstrip('/')}/{route_or_url.lstrip('/')}"

    if query_params:
        filtered = {k: v for k, v in query_params.items() if v is not None}
        if filtered:
            separator = "&" if "?" in url else "?"
            url = f"{url}{separator}{urlencode(filtered, doseq=True)}"
    return url


def parse_observed_fact(raw: Any) -> ObservedFact | None:
    """Parse a raw dict into ObservedFact, rejecting lesson payloads."""
    if not isinstance(raw, dict):
        return None
    record_kind = raw.get("record_kind") or raw.get("entity_type")
    if record_kind and record_kind not in {"observed_fact", "vad_observed_fact"}:
        return None
    candidate = raw.get("fact") if isinstance(raw.get("fact"), dict) else raw
    if not isinstance(candidate, dict):
        return None
    if "lesson_id" in candidate and "fact_id" not in candidate:
        return None
    try:
        return ObservedFact.model_validate(candidate)
    except Exception:
        return None


def parse_inferred_lesson(raw: Any) -> InferredLesson | None:
    """Parse a raw dict into InferredLesson, rejecting fact payloads."""
    if not isinstance(raw, dict):
        return None
    record_kind = raw.get("record_kind") or raw.get("entity_type")
    if record_kind and record_kind not in {"inferred_lesson", "vad_inferred_lesson"}:
        return None
    candidate = raw.get("lesson") if isinstance(raw.get("lesson"), dict) else raw
    if not isinstance(candidate, dict):
        return None
    if "fact_id" in candidate and "lesson_id" not in candidate:
        return None
    try:
        return InferredLesson.model_validate(candidate)
    except Exception:
        return None


def classify_lessons(
    lessons: list[InferredLesson],
    *,
    include_unapproved: bool,
    limit: int,
) -> tuple[list[InferredLesson], list[InferredLesson]]:
    """Split lessons strictly into policy-eligible approved vs advisory unapproved."""
    approved: list[InferredLesson] = []
    unapproved: list[InferredLesson] = []
    seen_ids: set[str] = set()

    for lesson in lessons:
        if lesson.lesson_id in seen_ids:
            continue
        seen_ids.add(lesson.lesson_id)
        if lesson.is_policy_eligible:
            approved.append(lesson)
        elif include_unapproved:
            unapproved.append(lesson)

    return approved[:limit], unapproved[:limit]


class GenericEndpointMemoryProvider:
    """External HTTP/MCP memory target adapter supporting arbitrary endpoint configs."""

    def __init__(
        self,
        config: MemoryProviderConfig,
        http_transport: HttpTransport | None = None,
        mcp_transport: McpTransport | None = None,
    ) -> None:
        self.config = config
        self.name: str = str(config.options.get("provider_name") or config.provider.value)
        self._http_transport = http_transport or default_http_transport
        self._has_custom_mcp = mcp_transport is not None
        self._mcp_transport = mcp_transport

    def _effective_transports(self) -> list[str]:
        if self.config.transport == MemoryTransport.BOTH:
            transports: list[str] = []
            if self.config.endpoint_url:
                transports.append("http")
            if self.config.mcp_endpoint or self._has_custom_mcp:
                transports.append("mcp")
            return transports or ["http", "mcp"]
        if (
            self.config.transport == MemoryTransport.MCP
            or self.config.provider == MemoryProviderKind.CUSTOM_MCP
            and self.config.transport == MemoryTransport.IN_PROCESS
        ):
            return ["mcp"]
        if self.config.transport == MemoryTransport.HTTP:
            return ["http"]
        if self.config.endpoint_url:
            return ["http"]
        if self.config.mcp_endpoint or self._has_custom_mcp:
            return ["mcp"]
        return ["http"]

    def _resolve_http_url(
        self,
        route_key: str,
        *,
        lesson_id: str | None = None,
        query_params: dict[str, Any] | None = None,
    ) -> str:
        route = self.config.extra_endpoints.get(route_key, _DEFAULT_HTTP_ROUTES[route_key])
        if lesson_id is not None:
            if "{lesson_id}" in route:
                route = route.format(lesson_id=lesson_id)
            elif route_key == "lesson_status" and route_key not in self.config.extra_endpoints:
                route = f"/lessons/{lesson_id}/status"
        return build_endpoint_url(self.config.endpoint_url, route, query_params=query_params)

    def _resolve_mcp_tool(self, op_key: str) -> str:
        mcp_key = f"mcp_{op_key}"
        if mcp_key in self.config.extra_endpoints:
            return self.config.extra_endpoints[mcp_key]
        if op_key in self.config.extra_endpoints:
            override = self.config.extra_endpoints[op_key]
            if not override.startswith(("/", "http://", "https://")):
                return override
        if op_key == "record_fact" and "facts" in self.config.extra_endpoints:
            override = self.config.extra_endpoints["facts"]
            if not override.startswith(("/", "http://", "https://")):
                return override
        if op_key == "record_lesson" and "lessons" in self.config.extra_endpoints:
            override = self.config.extra_endpoints["lessons"]
            if not override.startswith(("/", "http://", "https://")):
                return override
        return _DEFAULT_MCP_TOOLS[op_key]

    def _call_mcp(self, op_key: str, arguments: dict[str, Any]) -> dict[str, Any]:
        tool_name = self._resolve_mcp_tool(op_key)
        headers = resolve_auth_headers(self.config)
        payload = {"project_id": self.config.project_id, **arguments}
        if self._mcp_transport is not None:
            return invoke_mcp_transport(
                self._mcp_transport,
                tool_name,
                payload,
                headers,
                self.config.timeout_seconds,
            )
        if not self.config.mcp_endpoint:
            raise RemoteEndpointError("No mcp_endpoint configured")
        return default_mcp_transport(
            self.config.mcp_endpoint,
            tool_name,
            payload,
            headers,
            self.config.timeout_seconds,
        )

    def _call_http(
        self,
        method: str,
        route_key: str,
        *,
        payload: dict[str, Any] | None = None,
        lesson_id: str | None = None,
        query_params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = self._resolve_http_url(
            route_key,
            lesson_id=lesson_id,
            query_params=query_params,
        )
        headers = resolve_auth_headers(self.config)
        return invoke_http_transport(
            self._http_transport,
            method,
            url,
            payload,
            headers,
            self.config.timeout_seconds,
        )

    def _dispatch(
        self,
        http_fn: Callable[[], dict[str, Any]],
        mcp_fn: Callable[[], dict[str, Any]],
    ) -> dict[str, Any]:
        transports = self._effective_transports()
        if transports == ["mcp"]:
            return mcp_fn()
        if transports == ["http"]:
            return http_fn()

        last_exc: Exception | None = None
        for transport_name in transports:
            try:
                if transport_name == "http":
                    return http_fn()
                return mcp_fn()
            except Exception as exc:
                last_exc = exc
        assert last_exc is not None
        raise last_exc

    def capabilities(self) -> MemoryCapabilities:
        return MemoryCapabilities(
            fact_storage=True,
            lesson_storage=True,
            scoped_query=True,
            health_check=True,
            semantic_search=bool(self.config.options.get("semantic_search", False)),
            causal_graph=bool(self.config.options.get("causal_graph", False)),
            contradiction_resolution=bool(
                self.config.options.get("contradiction_resolution", False)
            ),
            context_consolidation=bool(self.config.options.get("context_consolidation", False)),
            provenance_traversal=bool(self.config.options.get("provenance_traversal", False)),
            supported_transports=self._effective_transports(),
        )

    def health(self) -> ProviderHealth:
        started = time.perf_counter()
        try:
            response = self._dispatch(
                lambda: self._call_http("GET", "health"),
                lambda: self._call_mcp("health", {}),
            )
            elapsed_ms = round((time.perf_counter() - started) * 1000.0, 3)
            healthy = bool(response.get("healthy", response.get("status", "ok") == "ok"))
            detail = str(response.get("detail") or response.get("status") or "ok")
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
        body = {
            "record_kind": "observed_fact",
            "project_id": self.config.project_id,
            "fact": fact.model_dump(mode="json"),
        }
        try:
            response = self._dispatch(
                lambda: self._call_http("POST", "facts", payload=body),
                lambda: self._call_mcp("record_fact", body),
            )
            return str(response.get("fact_id") or fact.fact_id)
        except Exception:
            if not self.config.fail_open_on_unavailable:
                raise
            return fact.fact_id

    def record_lesson(self, lesson: InferredLesson) -> str:
        body = {
            "record_kind": "inferred_lesson",
            "project_id": self.config.project_id,
            "lesson": lesson.model_dump(mode="json"),
        }
        try:
            response = self._dispatch(
                lambda: self._call_http("POST", "lessons", payload=body),
                lambda: self._call_mcp("record_lesson", body),
            )
            return str(response.get("lesson_id") or lesson.lesson_id)
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
        body: dict[str, Any] = {
            "lesson_id": lesson_id,
            "review_status": status.value,
            "reviewed_by": reviewed_by,
            "promotion_record_id": promotion_record_id,
        }
        try:
            response = self._dispatch(
                lambda: self._call_http(
                    "PATCH",
                    "lesson_status",
                    payload=body,
                    lesson_id=lesson_id,
                ),
                lambda: self._call_mcp("lesson_status", body),
            )
        except RemoteEndpointError as exc:
            if exc.status_code == 404:
                raise KeyError(f"Lesson not found: {lesson_id}") from exc
            raise

        lesson = parse_inferred_lesson(response.get("lesson", response))
        if lesson is None:
            raise KeyError(f"Lesson not found: {lesson_id}")
        return lesson

    def query(self, query: MemoryQuery) -> AdvisoryContext:
        body = {
            "project_id": self.config.project_id,
            "query_text": query.query_text,
            "domain_tags": query.domain_tags,
            "fact_types": [ft.value for ft in query.fact_types],
            "include_unapproved_lessons": query.include_unapproved_lessons,
            "limit": query.limit,
        }
        try:
            response = self._dispatch(
                lambda: self._call_http("POST", "query", payload=body),
                lambda: self._call_mcp("query", body),
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

        raw_facts = response.get("facts", [])
        facts: list[ObservedFact] = []
        if isinstance(raw_facts, list):
            for item in raw_facts:
                parsed = parse_observed_fact(item)
                if parsed is None:
                    continue
                if query.fact_types and parsed.fact_type not in query.fact_types:
                    continue
                facts.append(parsed)

        candidate_lessons: list[InferredLesson] = []
        for key in ("approved_lessons", "advisory_unapproved_lessons", "lessons"):
            bucket = response.get(key, [])
            if isinstance(bucket, list):
                for item in bucket:
                    parsed_lesson = parse_inferred_lesson(item)
                    if parsed_lesson is not None:
                        candidate_lessons.append(parsed_lesson)

        approved, unapproved = classify_lessons(
            candidate_lessons,
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
            response = self._dispatch(
                lambda: self._call_http(
                    "GET",
                    "facts",
                    query_params={"project_id": self.config.project_id, "limit": limit},
                ),
                lambda: self._call_mcp("list_facts", {"limit": limit}),
            )
        except Exception:
            if not self.config.fail_open_on_unavailable:
                raise
            return []

        raw_items = response.get("facts", response.get("results", []))
        if not isinstance(raw_items, list):
            return []
        facts = [
            parsed
            for item in raw_items
            if (parsed := parse_observed_fact(item)) is not None
        ]
        return facts[:limit]

    def list_lessons(
        self,
        status: LessonReviewStatus | None = None,
        limit: int = 50,
    ) -> list[InferredLesson]:
        if limit <= 0:
            return []
        query_params: dict[str, Any] = {
            "project_id": self.config.project_id,
            "limit": limit,
        }
        mcp_args: dict[str, Any] = {"limit": limit}
        if status is not None:
            query_params["status"] = status.value
            mcp_args["status"] = status.value

        try:
            response = self._dispatch(
                lambda: self._call_http("GET", "lessons", query_params=query_params),
                lambda: self._call_mcp("list_lessons", mcp_args),
            )
        except Exception:
            if not self.config.fail_open_on_unavailable:
                raise
            return []

        raw_items = response.get("lessons", response.get("results", []))
        if not isinstance(raw_items, list):
            return []
        lessons = [
            parsed
            for item in raw_items
            if (parsed := parse_inferred_lesson(item)) is not None
            and (status is None or parsed.review_status == status)
        ]
        return lessons[:limit]
