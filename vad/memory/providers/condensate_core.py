from __future__ import annotations

import json
import os
import shlex
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from vad.memory.capabilities import MemoryCapabilities, ProviderHealth
from vad.memory.config import MemoryProviderConfig, MemoryTransport
from vad.memory.contracts import (
    AdvisoryContext,
    InferredLesson,
    LessonReviewStatus,
    MemoryQuery,
    ObservedFact,
)

HttpTransport = Callable[[str, str, dict[str, Any] | None, dict[str, str]], dict[str, Any]]
McpTransport = Callable[[str, dict[str, Any]], dict[str, Any]]

_DEFAULT_HTTP_ROUTES: dict[str, str] = {
    "health": "/api/v1/health",
    "facts": "/api/v1/facts",
    "lessons": "/api/v1/lessons",
    "lesson_status": "/api/v1/lessons/{lesson_id}/status",
    "query": "/api/v1/query",
}

_SUPPORTED_TRANSPORTS = frozenset(
    {MemoryTransport.HTTP, MemoryTransport.MCP, MemoryTransport.BOTH}
)


class CondensateCoreProvider:
    """Reference MemoryProviderProtocol adapter for Condensate Core (HTTP + MCP)."""

    name: str = "condensate_core"

    def __init__(
        self,
        config: MemoryProviderConfig,
        http_transport: HttpTransport | None = None,
        mcp_transport: McpTransport | None = None,
    ) -> None:
        if config.transport not in _SUPPORTED_TRANSPORTS:
            raise ValueError(
                f"CondensateCoreProvider requires transport in {{'http', 'mcp', 'both'}}, "
                f"got {config.transport.value!r}"
            )
        has_http = bool(config.endpoint_url and config.endpoint_url.strip())
        has_mcp = bool(config.mcp_endpoint and config.mcp_endpoint.strip())
        if config.transport == MemoryTransport.HTTP and not has_http:
            raise ValueError("CondensateCoreProvider with transport='http' requires endpoint_url")
        if config.transport == MemoryTransport.MCP and not has_mcp:
            raise ValueError("CondensateCoreProvider with transport='mcp' requires mcp_endpoint")
        if config.transport == MemoryTransport.BOTH and not (has_http or has_mcp):
            raise ValueError(
                "CondensateCoreProvider with transport='both' requires endpoint_url or mcp_endpoint"
            )

        self.config = config
        self._http_transport_override = http_transport
        self._mcp_transport_override = mcp_transport

    def capabilities(self) -> MemoryCapabilities:
        return MemoryCapabilities(
            fact_storage=True,
            lesson_storage=True,
            scoped_query=True,
            health_check=True,
            semantic_search=True,
            causal_graph=True,
            contradiction_resolution=True,
            context_consolidation=True,
            provenance_traversal=True,
            supported_transports=["http", "mcp", "both"],
        )

    def health(self) -> ProviderHealth:
        started = time.perf_counter()
        try:
            transport_used, payload = self._execute_with_failover(
                http_call=lambda: self._http_request(
                    "GET", self._resolve_http_url("health"), None
                ),
                mcp_call=lambda: self._mcp_call(
                    "condensate_health", {"project_id": self.config.project_id}
                ),
            )
        except (ConnectionError, OSError, RuntimeError, ValueError) as exc:
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            return ProviderHealth(
                healthy=False,
                provider_name=self.name,
                latency_ms=round(elapsed_ms, 3),
                detail=self._sanitize_message(str(exc)),
            )

        elapsed_ms = (time.perf_counter() - started) * 1000.0
        detail = str(payload.get("detail") or payload.get("status") or f"ok via {transport_used}")
        return ProviderHealth(
            healthy=bool(payload.get("healthy", payload.get("ok", True))),
            provider_name=self.name,
            latency_ms=round(elapsed_ms, 3),
            detail=self._sanitize_message(detail),
        )

    def record_fact(self, fact: ObservedFact) -> str:
        fact_payload = fact.model_dump(mode="json")
        _, response = self._execute_with_failover(
            http_call=lambda: self._http_request(
                "POST",
                self._resolve_http_url("facts"),
                {"project_id": self.config.project_id, "fact": fact_payload},
            ),
            mcp_call=lambda: self._mcp_call(
                "condensate_record_fact",
                {"project_id": self.config.project_id, "fact": fact_payload},
            ),
        )
        return str(response.get("fact_id") or fact.fact_id)

    def record_lesson(self, lesson: InferredLesson) -> str:
        lesson_payload = lesson.model_dump(mode="json")
        _, response = self._execute_with_failover(
            http_call=lambda: self._http_request(
                "POST",
                self._resolve_http_url("lessons"),
                {"project_id": self.config.project_id, "lesson": lesson_payload},
            ),
            mcp_call=lambda: self._mcp_call(
                "condensate_record_lesson",
                {"project_id": self.config.project_id, "lesson": lesson_payload},
            ),
        )
        return str(response.get("lesson_id") or lesson.lesson_id)

    def update_lesson_status(
        self,
        lesson_id: str,
        status: LessonReviewStatus,
        reviewed_by: str,
        promotion_record_id: str | None = None,
    ) -> InferredLesson:
        body: dict[str, Any] = {
            "project_id": self.config.project_id,
            "lesson_id": lesson_id,
            "status": status.value,
            "reviewed_by": reviewed_by,
            "promotion_record_id": promotion_record_id,
        }
        _, response = self._execute_with_failover(
            http_call=lambda: self._http_request(
                "POST",
                self._resolve_http_url("lesson_status", lesson_id=lesson_id),
                body,
            ),
            mcp_call=lambda: self._mcp_call("condensate_update_lesson_status", body),
        )
        lesson_data = response.get("lesson", response)
        return InferredLesson.model_validate(lesson_data)

    def query(self, query: MemoryQuery) -> AdvisoryContext:
        query_payload = query.model_dump(mode="json")
        try:
            _, response = self._execute_with_failover(
                http_call=lambda: self._http_request(
                    "POST",
                    self._resolve_http_url("query"),
                    {"project_id": self.config.project_id, "query": query_payload},
                ),
                mcp_call=lambda: self._mcp_call(
                    "condensate_query",
                    {"project_id": self.config.project_id, "query": query_payload},
                ),
            )
        except (ConnectionError, OSError, RuntimeError) as exc:
            if not self.config.fail_open_on_unavailable:
                raise
            return AdvisoryContext(
                provider_name=self.name,
                facts=[],
                approved_lessons=[],
                advisory_unapproved_lessons=[],
                degraded=True,
                degraded_reason=self._sanitize_message(str(exc)),
            )

        return self._build_advisory_context(response, query)

    def list_facts(self, limit: int = 50) -> list[ObservedFact]:
        if limit <= 0:
            return []
        params = urlencode({"limit": limit, "project_id": self.config.project_id})
        _, response = self._execute_with_failover(
            http_call=lambda: self._http_request(
                "GET", f"{self._resolve_http_url('facts')}?{params}", None
            ),
            mcp_call=lambda: self._mcp_call(
                "condensate_list_facts",
                {"project_id": self.config.project_id, "limit": limit},
            ),
        )
        raw_facts = response.get("facts", response.get("items", []))
        return [ObservedFact.model_validate(item) for item in raw_facts][:limit]

    def list_lessons(
        self,
        status: LessonReviewStatus | None = None,
        limit: int = 50,
    ) -> list[InferredLesson]:
        if limit <= 0:
            return []
        query_dict: dict[str, Any] = {
            "limit": limit,
            "project_id": self.config.project_id,
        }
        if status is not None:
            query_dict["status"] = status.value
        params = urlencode(query_dict)
        _, response = self._execute_with_failover(
            http_call=lambda: self._http_request(
                "GET", f"{self._resolve_http_url('lessons')}?{params}", None
            ),
            mcp_call=lambda: self._mcp_call(
                "condensate_list_lessons",
                {
                    "project_id": self.config.project_id,
                    "status": status.value if status is not None else None,
                    "limit": limit,
                },
            ),
        )
        raw_lessons = response.get("lessons", response.get("items", []))
        lessons = [InferredLesson.model_validate(item) for item in raw_lessons]
        if status is not None:
            lessons = [lesson for lesson in lessons if lesson.review_status == status]
        return lessons[:limit]

    # ------------------------------------------------------------------
    # Internal transport, failover, secret resolution & parsing helpers
    # ------------------------------------------------------------------

    def _transport_order(self) -> list[str]:
        if self.config.transport == MemoryTransport.HTTP:
            return ["http"]
        if self.config.transport == MemoryTransport.MCP:
            return ["mcp"]

        has_http = bool(self.config.endpoint_url and self.config.endpoint_url.strip())
        has_mcp = bool(self.config.mcp_endpoint and self.config.mcp_endpoint.strip()) or (
            self._mcp_transport_override is not None
        )
        order: list[str] = []
        if has_http:
            order.append("http")
            if has_mcp:
                order.append("mcp")
        elif has_mcp:
            order.append("mcp")
        return order

    def _execute_with_failover(
        self,
        http_call: Callable[[], dict[str, Any]],
        mcp_call: Callable[[], dict[str, Any]],
    ) -> tuple[str, dict[str, Any]]:
        order = self._transport_order()
        failures: list[tuple[str, Exception]] = []
        for transport_name in order:
            try:
                if transport_name == "http":
                    return "http", http_call()
                return "mcp", mcp_call()
            except (ConnectionError, OSError, RuntimeError) as exc:
                failures.append((transport_name, exc))

        last_exc = failures[-1][1]
        if len(failures) == 1:
            raise last_exc
        summary = "; ".join(
            f"{name}: {self._sanitize_message(str(err))}" for name, err in failures
        )
        raise type(last_exc)(f"All transports failed ({summary})") from last_exc

    def _resolve_http_url(self, route_key: str, **path_params: str) -> str:
        base = (self.config.endpoint_url or "").strip().rstrip("/")
        raw_route = self.config.extra_endpoints.get(route_key, _DEFAULT_HTTP_ROUTES[route_key])
        formatted_route = raw_route.format(**path_params) if path_params else raw_route
        if formatted_route.startswith(("http://", "https://")):
            return formatted_route
        if not formatted_route.startswith("/"):
            formatted_route = f"/{formatted_route}"
        return f"{base}{formatted_route}"

    def _resolve_secret_token(self) -> str | None:
        """Resolve auth_secret_ref on demand without caching raw secret material."""
        ref = self.config.auth_secret_ref
        if not ref:
            return None
        prefix, _, target = ref.partition(":")
        target = target.strip()
        if prefix == "env":
            value = os.environ.get(target)
            return value if value else None
        if prefix == "file":
            path = Path(target)
            if path.is_file():
                content = path.read_text(encoding="utf-8").strip()
                return content if content else None
        return None

    def _sanitize_message(self, message: str) -> str:
        token = self._resolve_secret_token()
        if token and token in message:
            return message.replace(token, "[REDACTED]")
        return message

    def _build_headers(self) -> dict[str, str]:
        headers: dict[str, str] = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-VAD-Project-ID": self.config.project_id,
        }
        token = self._resolve_secret_token()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    def _http_request(
        self,
        method: str,
        url: str,
        payload: dict[str, Any] | None,
    ) -> dict[str, Any]:
        headers = self._build_headers()
        if self._http_transport_override is not None:
            response = self._http_transport_override(method, url, payload, headers)
            return response if isinstance(response, dict) else {}
        return self._default_http_transport(method, url, payload, headers)

    def _default_http_transport(
        self,
        method: str,
        url: str,
        payload: dict[str, Any] | None,
        headers: dict[str, str],
    ) -> dict[str, Any]:
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = Request(url=url, data=body, headers=headers, method=method)
        try:
            with urlopen(req, timeout=self.config.timeout_seconds) as resp:
                raw_text = resp.read().decode("utf-8").strip()
                if not raw_text:
                    return {}
                parsed = json.loads(raw_text)
                if not isinstance(parsed, dict):
                    raise RuntimeError(f"Expected JSON object from {url}")
                return parsed
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace") if exc.fp else str(exc)
            if exc.code == 404:
                raise KeyError(detail or f"Resource not found at {url}") from exc
            raise RuntimeError(f"HTTP {exc.code} from {url}: {detail}") from exc
        except URLError as exc:
            raise ConnectionError(f"HTTP connection failed for {url}: {exc.reason}") from exc
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Invalid JSON response from {url}: {exc}") from exc

    def _mcp_call(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if self._mcp_transport_override is not None:
            raw_response = self._mcp_transport_override(tool_name, arguments)
        else:
            raw_response = self._default_mcp_transport(tool_name, arguments)
        return self._unwrap_mcp_response(raw_response)

    def _default_mcp_transport(
        self,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        endpoint = (self.config.mcp_endpoint or "").strip()
        if not endpoint:
            raise ValueError("mcp_endpoint is required for MCP transport")

        rpc_request: dict[str, Any] = {
            "jsonrpc": "2.0",
            "id": str(uuid.uuid4()),
            "method": "tools/call",
            "params": {
                "name": tool_name,
                "arguments": arguments,
            },
        }

        if endpoint.startswith(("http://", "https://")):
            return self._default_http_transport(
                "POST",
                endpoint,
                rpc_request,
                self._build_headers(),
            )

        command = endpoint[len("stdio://") :] if endpoint.startswith("stdio://") else endpoint
        argv = shlex.split(command, posix=(os.name != "nt"))
        if not argv:
            raise ValueError("Empty stdio MCP command")

        try:
            proc = subprocess.run(
                argv,
                input=json.dumps(rpc_request) + "\n",
                text=True,
                capture_output=True,
                timeout=self.config.timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise ConnectionError(f"MCP stdio timeout invoking {tool_name}") from exc
        except OSError as exc:
            raise ConnectionError(f"MCP stdio failed to start {argv[0]}: {exc}") from exc

        if proc.returncode != 0:
            raise RuntimeError(
                f"MCP stdio command exited with {proc.returncode}: {proc.stderr.strip()}"
            )

        lines = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
        if not lines:
            return {}
        try:
            parsed = json.loads(lines[-1])
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Invalid JSON-RPC output from MCP stdio: {exc}") from exc
        if not isinstance(parsed, dict):
            raise RuntimeError("Expected JSON-RPC object from MCP stdio")
        return parsed

    @staticmethod
    def _unwrap_mcp_response(response: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(response, dict):
            raise RuntimeError("Expected dict response from MCP transport")

        error = response.get("error")
        if error:
            message = error.get("message", str(error)) if isinstance(error, dict) else str(error)
            code = error.get("code") if isinstance(error, dict) else None
            if code == -32004 or "not found" in message.lower():
                raise KeyError(message)
            raise RuntimeError(f"MCP JSON-RPC error: {message}")

        result = response.get("result", response)
        if not isinstance(result, dict):
            raise RuntimeError("Expected dict result in MCP response")

        if result.get("isError"):
            content = result.get("content") or []
            err_text = (
                content[0].get("text", "MCP tool error")
                if content and isinstance(content[0], dict)
                else "MCP tool error"
            )
            if "not found" in err_text.lower():
                raise KeyError(err_text)
            raise RuntimeError(err_text)

        structured = result.get("structuredContent")
        if isinstance(structured, dict):
            return structured

        content = result.get("content")
        if isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    text = item.get("text", "")
                    if text.strip():
                        try:
                            decoded = json.loads(text)
                            if isinstance(decoded, dict):
                                return decoded
                        except json.JSONDecodeError:
                            pass

        return result

    def _build_advisory_context(
        self,
        payload: dict[str, Any],
        query: MemoryQuery,
    ) -> AdvisoryContext:
        facts = [ObservedFact.model_validate(item) for item in payload.get("facts", [])]
        if query.fact_types:
            allowed_types = set(query.fact_types)
            facts = [fact for fact in facts if fact.fact_type in allowed_types]

        candidate_lesson_dicts: list[Any] = []
        for key in ("approved_lessons", "advisory_unapproved_lessons", "lessons"):
            raw_list = payload.get(key)
            if isinstance(raw_list, list):
                candidate_lesson_dicts.extend(raw_list)

        approved_lessons: list[InferredLesson] = []
        advisory_unapproved_lessons: list[InferredLesson] = []
        seen_lesson_ids: set[str] = set()

        for raw_item in candidate_lesson_dicts:
            lesson = InferredLesson.model_validate(raw_item)
            if lesson.lesson_id in seen_lesson_ids:
                continue
            seen_lesson_ids.add(lesson.lesson_id)
            if lesson.is_policy_eligible:
                approved_lessons.append(lesson)
            elif query.include_unapproved_lessons:
                advisory_unapproved_lessons.append(lesson)

        return AdvisoryContext(
            provider_name=self.name,
            facts=facts[: query.limit],
            approved_lessons=approved_lessons[: query.limit],
            advisory_unapproved_lessons=(
                advisory_unapproved_lessons[: query.limit]
                if query.include_unapproved_lessons
                else []
            ),
            degraded=bool(payload.get("degraded", False)),
            degraded_reason=(
                self._sanitize_message(str(payload["degraded_reason"]))
                if payload.get("degraded_reason") is not None
                else None
            ),
        )
