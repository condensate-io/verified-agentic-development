from __future__ import annotations

from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator

from vad.control_plane.events import ControlPlaneEvent, ControlPlaneEventKind, ControlPlaneEventStatus
from vad.control_plane.sdk import LocalControlPlaneClient
from vad.control_plane.sessions import HostRole, HostSessionManager, HostSessionToken
from vad.policy.decisions import PolicyDecision


class GatewayToolRisk(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class GatewayToolEventPolicy(BaseModel):
    emits_started: bool = True
    emits_finished: bool = True
    emits_failed: bool = True
    started_kind: ControlPlaneEventKind = ControlPlaneEventKind.TOOL_CALL_STARTED
    finished_kind: ControlPlaneEventKind = ControlPlaneEventKind.TOOL_CALL_FINISHED
    failed_kind: ControlPlaneEventKind = ControlPlaneEventKind.TOOL_CALL_FINISHED


class GatewayToolDefinition(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1)
    input_schema: dict[str, Any]
    risk: GatewayToolRisk
    required_role: str
    event_policy: GatewayToolEventPolicy = Field(default_factory=GatewayToolEventPolicy)
    high_risk: bool = False

    @field_validator("name", "required_role")
    @classmethod
    def identifiers_must_be_safe(cls, value: str) -> str:
        if any(separator in value for separator in ["/", "\\", "\n", "\r", "\t"]):
            raise ValueError("gateway tool identifiers must not contain path or control separators")
        return value

    def to_mcp_tool(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.input_schema,
        }


class GatewayToolVisibilityRequest(BaseModel):
    client_id: str | None = Field(default=None, max_length=160)
    run_id: str | None = Field(default=None, max_length=160)
    role: str | None = Field(default=None, max_length=80)
    approved_high_risk_tools: tuple[str, ...] = ()
    host_session: HostSessionToken | dict[str, Any] | None = None
    work_item_id: str | None = Field(default=None, max_length=160)
    min_fence_token: int | None = Field(default=None, ge=1)

    @field_validator("client_id", "run_id", "role", "work_item_id")
    @classmethod
    def identifiers_must_be_safe(cls, value: str | None) -> str | None:
        if value is None:
            return value
        if any(separator in value for separator in ["/", "\\", "\n", "\r", "\t"]):
            raise ValueError("gateway visibility identifiers must not contain path or control separators")
        return value

    @field_validator("approved_high_risk_tools")
    @classmethod
    def approved_tools_must_be_safe(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(sorted(set(value.strip() for value in values if value.strip())))
        if any(any(separator in value for separator in ["/", "\\", "\n", "\r", "\t"]) for value in normalized):
            raise ValueError("approved tool names must not contain path or control separators")
        return normalized


class GatewayToolAuditRecord(BaseModel):
    tool_name: str
    client_id: str | None = None
    run_id: str | None = None
    requested_role: str | None = None
    required_role: str
    visible: bool
    reason: str


class GatewayToolVisibilityResult(BaseModel):
    tools: tuple[GatewayToolDefinition, ...]
    audit: tuple[GatewayToolAuditRecord, ...]

    def to_mcp_tools(self) -> list[dict[str, Any]]:
        return [tool.to_mcp_tool() for tool in self.tools]


class McpToolCallRecorder(BaseModel):
    db_path: Path
    client_id: str
    actor_id: str
    role: str
    run_id: str | None = None
    task_id: str | None = None

    @field_validator("client_id", "actor_id", "role", "run_id", "task_id")
    @classmethod
    def identifiers_must_be_safe(cls, value: str | None) -> str | None:
        if value is None:
            return value
        if any(separator in value for separator in ["/", "\\", "\n", "\r", "\t"]):
            raise ValueError("MCP event identifiers must not contain path or control separators")
        return value

    def record_started(self, *, tool_name: str, args: dict[str, Any]) -> ControlPlaneEvent:
        return self._emit(
            tool_name=tool_name,
            status=ControlPlaneEventStatus.ACTIVE,
            summary=f"MCP tool {tool_name} started with args {_bounded_summary(json_safe(redact_tool_arguments(args)))}.",
        )

    def record_finished(
        self,
        *,
        tool_name: str,
        result: dict[str, Any],
        evidence_digest: str | None = None,
    ) -> ControlPlaneEvent:
        failed = bool(result.get("isError", False))
        status = ControlPlaneEventStatus.FAILED if failed else ControlPlaneEventStatus.PASSED
        digest = evidence_digest or result.get("mcp_attribution", {}).get("evidence_digest")
        return self._emit(
            tool_name=tool_name,
            status=status,
            evidence_digest=digest,
            summary=f"MCP tool {tool_name} {'failed' if failed else 'finished'}; evidence_digest={digest or 'none'}.",
        )

    def record_denied(self, *, tool_name: str, reason: str) -> ControlPlaneEvent:
        return self._emit(
            tool_name=tool_name,
            status=ControlPlaneEventStatus.BLOCKED,
            summary=f"MCP tool {tool_name} denied: {reason}.",
        )

    def _emit(
        self,
        *,
        tool_name: str,
        status: ControlPlaneEventStatus,
        summary: str,
        evidence_digest: str | None = None,
    ) -> ControlPlaneEvent:
        event = ControlPlaneEvent(
            sequence=_next_event_sequence(self.db_path),
            client_id=self.client_id,
            run_id=self.run_id,
            task_id=self.task_id,
            kind=ControlPlaneEventKind.TOOL_CALL_STARTED if status == ControlPlaneEventStatus.ACTIVE else ControlPlaneEventKind.TOOL_CALL_FINISHED,
            status=status,
            actor=self.actor_id,
            role=self.role,
            evidence_digest=evidence_digest,
            summary=summary,
        )
        LocalControlPlaneClient.from_db_path(self.db_path).emit_event(event)
        return event


SENSITIVE_ARGUMENT_MARKERS = ("secret", "token", "password", "api_key", "private_key", "key")


def redact_tool_arguments(args: dict[str, Any]) -> dict[str, Any]:
    redacted: dict[str, Any] = {}
    for key, value in args.items():
        lowered = key.lower()
        if any(marker in lowered for marker in SENSITIVE_ARGUMENT_MARKERS):
            redacted[key] = "[REDACTED]"
        elif isinstance(value, dict):
            redacted[key] = redact_tool_arguments(value)
        elif isinstance(value, list):
            redacted[key] = [redact_tool_arguments(item) if isinstance(item, dict) else item for item in value]
        else:
            redacted[key] = value
    return redacted


def json_safe(payload: dict[str, Any]) -> str:
    import json

    return json.dumps(payload, sort_keys=True)


def _bounded_summary(value: str, *, limit: int = 360) -> str:
    if len(value) <= limit:
        return value
    return f"{value[:limit - 14]}...[truncated]"


def gateway_tool_registry() -> dict[str, GatewayToolDefinition]:
    return {tool.name: tool for tool in _GATEWAY_TOOLS}


def filter_gateway_tools(request: GatewayToolVisibilityRequest | None = None) -> GatewayToolVisibilityResult:
    visibility = request or GatewayToolVisibilityRequest(role="observer")
    role = visibility.role or "observer"
    visible = []
    audit = []
    for tool in _GATEWAY_TOOLS:
        allowed, reason = _tool_visible(tool, role, visibility)
        if allowed:
            visible.append(tool)
        audit.append(GatewayToolAuditRecord(
            tool_name=tool.name,
            client_id=visibility.client_id,
            run_id=visibility.run_id,
            requested_role=role,
            required_role=tool.required_role,
            visible=allowed,
            reason=reason,
        ))
    return GatewayToolVisibilityResult(tools=tuple(visible), audit=tuple(audit))


def mcp_tool_definitions(request: GatewayToolVisibilityRequest | None = None) -> list[dict[str, Any]]:
    return filter_gateway_tools(request).to_mcp_tools()


GATEWAY_TOOL_ACTIONS: dict[str, str] = {
    "run_proofs": "submit_verification_result",
    "provider_test": "submit_verification_result",
    "retro_analyze": "promote_lesson",
    "submit_retrospective": "promote_lesson",
    "repo_patch": "submit_diff_proposal",
    "repo_run": "submit_diff_proposal",
    "sign_verify": "approve_release",
    "work_intake_discover": "discover_work",
    "work_intake_claim": "claim_work",
    "work_intake_report_progress": "report_progress",
    "work_intake_submit_proof": "submit_builder_claim",
    "work_intake_escalate": "escalate",
}


def _parse_host_session_token(
    token: HostSessionToken | dict[str, Any] | None,
) -> tuple[HostSessionToken | None, str | None]:
    if token is None:
        return None, "host session token is required"
    if isinstance(token, HostSessionToken):
        return token, None
    if isinstance(token, dict):
        try:
            return HostSessionToken.model_validate(token), None
        except Exception as exc:
            return None, f"invalid host session token: {exc}"
    return None, "host session token must be a HostSessionToken or dict"


def _effective_gateway_role(tool: GatewayToolDefinition | None, host_role: HostRole) -> str:
    if (
        tool is not None
        and tool.required_role == "developer"
        and host_role in (HostRole.BUILDER, HostRole.VERIFIER)
    ):
        return "developer"
    return host_role.value


def _role_matches_claim(requested_role: str | None, claim_role: HostRole) -> bool:
    if requested_role is None:
        return True
    if requested_role == claim_role.value:
        return True
    return requested_role == "developer" and claim_role in (HostRole.BUILDER, HostRole.VERIFIER)


def authorize_host_session(
    session_manager: HostSessionManager,
    token: HostSessionToken | dict[str, Any],
    *,
    action: str,
    work_item_id: str | None = None,
    min_fence_token: int | None = None,
    client_id: str | None = None,
    now: datetime | None = None,
) -> PolicyDecision:
    """Verify a scoped host session token and enforce role-action isolation."""
    if session_manager is None:
        return PolicyDecision(allow=False, denials=["session manager is not configured"])

    parsed, parse_err = _parse_host_session_token(token)
    if parsed is None:
        return PolicyDecision(allow=False, denials=[parse_err or "invalid host session token"])

    ok, verify_err = session_manager.verify(
        parsed,
        now=now,
        expected_work_item_id=work_item_id,
        min_fence_token=min_fence_token,
    )
    if not ok:
        return PolicyDecision(allow=False, denials=[verify_err or "host session verification failed"])

    if client_id is not None and parsed.claims.client_id != client_id:
        return PolicyDecision(
            allow=False,
            denials=[f"client_id mismatch: expected {client_id}, got {parsed.claims.client_id}"],
        )

    allowed, role_err = session_manager.assert_role_action_allowed(parsed.claims, action)
    if not allowed:
        return PolicyDecision(
            allow=False,
            denials=[role_err or f"action '{action}' is not allowed for role '{parsed.claims.role.value}'"],
        )

    return PolicyDecision(
        allow=True,
        reasons=[
            f"host session '{parsed.claims.session_id}' ({parsed.claims.host_type}:{parsed.claims.role.value}) authorized for '{action}'"
        ],
    )


def _authorize_with_host_session(
    *,
    tool_name: str,
    tool: GatewayToolDefinition | None,
    visibility: GatewayToolVisibilityRequest,
    session_manager: HostSessionManager | None,
    token_input: HostSessionToken | dict[str, Any],
    action: str | None,
    work_item_id: str | None,
    min_fence_token: int | None,
    now: datetime | None,
) -> GatewayToolAuditRecord:
    required_role = tool.required_role if tool is not None else "unknown"
    if session_manager is None:
        return GatewayToolAuditRecord(
            tool_name=tool_name,
            client_id=visibility.client_id,
            run_id=visibility.run_id,
            requested_role=visibility.role,
            required_role=required_role,
            visible=False,
            reason="session manager is not configured",
        )

    parsed, parse_err = _parse_host_session_token(token_input)
    if parsed is None:
        return GatewayToolAuditRecord(
            tool_name=tool_name,
            client_id=visibility.client_id,
            run_id=visibility.run_id,
            requested_role=visibility.role,
            required_role=required_role,
            visible=False,
            reason=parse_err or "invalid host session token",
        )

    claims = parsed.claims
    if visibility.client_id is not None and visibility.client_id != claims.client_id:
        return GatewayToolAuditRecord(
            tool_name=tool_name,
            client_id=visibility.client_id,
            run_id=visibility.run_id,
            requested_role=claims.role.value,
            required_role=required_role,
            visible=False,
            reason=f"client_id mismatch: expected {visibility.client_id}, got {claims.client_id}",
        )

    if not _role_matches_claim(visibility.role, claims.role):
        return GatewayToolAuditRecord(
            tool_name=tool_name,
            client_id=claims.client_id,
            run_id=visibility.run_id,
            requested_role=visibility.role,
            required_role=required_role,
            visible=False,
            reason=f"role mismatch: expected {visibility.role}, got {claims.role.value}",
        )

    if action is not None or tool_name in GATEWAY_TOOL_ACTIONS or tool is None or tool.required_role != "observer":
        effective_action = action or GATEWAY_TOOL_ACTIONS.get(tool_name, tool_name)
        decision = authorize_host_session(
            session_manager,
            parsed,
            action=effective_action,
            work_item_id=work_item_id,
            min_fence_token=min_fence_token,
            now=now,
        )
        if not decision.allow:
            return GatewayToolAuditRecord(
                tool_name=tool_name,
                client_id=claims.client_id,
                run_id=visibility.run_id,
                requested_role=claims.role.value,
                required_role=required_role,
                visible=False,
                reason=decision.denials[0] if decision.denials else "host session denied",
            )
    else:
        ok, err = session_manager.verify(
            parsed,
            now=now,
            expected_work_item_id=work_item_id,
            min_fence_token=min_fence_token,
        )
        if not ok:
            return GatewayToolAuditRecord(
                tool_name=tool_name,
                client_id=claims.client_id,
                run_id=visibility.run_id,
                requested_role=claims.role.value,
                required_role=required_role,
                visible=False,
                reason=err or "host session verification failed",
            )

    if tool is None:
        return GatewayToolAuditRecord(
            tool_name=tool_name,
            client_id=claims.client_id,
            run_id=visibility.run_id,
            requested_role=claims.role.value,
            required_role="unknown",
            visible=False,
            reason="unknown tool",
        )

    effective_role = _effective_gateway_role(tool, claims.role)
    allowed, reason = _tool_visible(tool, effective_role, visibility)
    return GatewayToolAuditRecord(
        tool_name=tool.name,
        client_id=claims.client_id,
        run_id=visibility.run_id,
        requested_role=claims.role.value,
        required_role=tool.required_role,
        visible=allowed,
        reason=reason,
    )


def authorize_gateway_tool_call(
    tool_name: str,
    request: GatewayToolVisibilityRequest | None = None,
    *,
    session_manager: HostSessionManager | None = None,
    host_session: HostSessionToken | dict[str, Any] | None = None,
    require_host_session: bool = False,
    action: str | None = None,
    work_item_id: str | None = None,
    min_fence_token: int | None = None,
    now: datetime | None = None,
) -> GatewayToolAuditRecord:
    visibility = request or GatewayToolVisibilityRequest(role=None if host_session is not None else "observer")
    registry = gateway_tool_registry()
    tool = registry.get(tool_name)
    token_input = host_session if host_session is not None else visibility.host_session
    eff_work_item_id = work_item_id if work_item_id is not None else visibility.work_item_id
    eff_min_fence = min_fence_token if min_fence_token is not None else visibility.min_fence_token

    if token_input is not None:
        return _authorize_with_host_session(
            tool_name=tool_name,
            tool=tool,
            visibility=visibility,
            session_manager=session_manager,
            token_input=token_input,
            action=action,
            work_item_id=eff_work_item_id,
            min_fence_token=eff_min_fence,
            now=now,
        )

    role = visibility.role or "observer"
    if require_host_session:
        return GatewayToolAuditRecord(
            tool_name=tool_name,
            client_id=visibility.client_id,
            run_id=visibility.run_id,
            requested_role=role,
            required_role=tool.required_role if tool is not None else "unknown",
            visible=False,
            reason="host session token is required",
        )

    if tool is None:
        return GatewayToolAuditRecord(
            tool_name=tool_name,
            client_id=visibility.client_id,
            run_id=visibility.run_id,
            requested_role=role,
            required_role="unknown",
            visible=False,
            reason="unknown tool",
        )
    allowed, reason = _tool_visible(tool, role, visibility)
    return GatewayToolAuditRecord(
        tool_name=tool.name,
        client_id=visibility.client_id,
        run_id=visibility.run_id,
        requested_role=role,
        required_role=tool.required_role,
        visible=allowed,
        reason=reason,
    )


class MCPGateway:
    """Governed MCP gateway enforcing tool visibility and scoped host session isolation."""

    def __init__(
        self,
        *,
        session_manager: HostSessionManager | None = None,
        require_host_session: bool = False,
    ) -> None:
        self.session_manager = session_manager
        self.require_host_session = require_host_session

    @staticmethod
    def _coerce_visibility_request(
        request: GatewayToolVisibilityRequest | dict[str, Any] | None,
    ) -> tuple[GatewayToolVisibilityRequest, str | None, int | None]:
        if request is None:
            return GatewayToolVisibilityRequest(role=None), None, None
        if isinstance(request, GatewayToolVisibilityRequest):
            return request, None, None
        payload = request.get("arguments") if isinstance(request.get("arguments"), dict) else request
        approved = payload.get("approved_high_risk_tools") or ()
        visibility = GatewayToolVisibilityRequest(
            client_id=payload.get("client_id"),
            run_id=payload.get("run_id"),
            role=payload.get("role") or payload.get("client_role"),
            approved_high_risk_tools=tuple(approved),
            host_session=payload.get("host_session") if "host_session" in payload else request.get("host_session"),
            work_item_id=payload.get("work_item_id"),
            min_fence_token=payload.get("min_fence_token"),
        )
        req_fence = payload.get("fence_token")
        parsed_fence = int(req_fence) if req_fence is not None else None
        req_action = payload.get("action") or request.get("action")
        return visibility, req_action, parsed_fence

    def authorize_session(
        self,
        token: HostSessionToken | dict[str, Any] | None,
        *,
        action: str,
        work_item_id: str | None = None,
        min_fence_token: int | None = None,
        client_id: str | None = None,
        now: datetime | None = None,
    ) -> PolicyDecision:
        if token is None:
            if self.require_host_session:
                return PolicyDecision(allow=False, denials=["host session token is required"])
            return PolicyDecision(allow=True, reasons=["host session omitted and not required"])
        if self.session_manager is None:
            return PolicyDecision(allow=False, denials=["session manager is not configured"])
        return authorize_host_session(
            self.session_manager,
            token,
            action=action,
            work_item_id=work_item_id,
            min_fence_token=min_fence_token,
            client_id=client_id,
            now=now,
        )

    def authorize_tool_call(
        self,
        tool_name: str,
        request: GatewayToolVisibilityRequest | dict[str, Any] | None = None,
        *,
        host_session: HostSessionToken | dict[str, Any] | None = None,
        action: str | None = None,
        work_item_id: str | None = None,
        min_fence_token: int | None = None,
        now: datetime | None = None,
    ) -> GatewayToolAuditRecord:
        visibility, req_action, req_fence = self._coerce_visibility_request(request)
        token_input = host_session if host_session is not None else visibility.host_session
        if token_input is not None and req_fence is not None:
            parsed, parse_err = _parse_host_session_token(token_input)
            if parsed is None:
                return GatewayToolAuditRecord(
                    tool_name=tool_name,
                    client_id=visibility.client_id,
                    run_id=visibility.run_id,
                    requested_role=visibility.role,
                    required_role="unknown",
                    visible=False,
                    reason=parse_err or "invalid host session token",
                )
            if req_fence != parsed.claims.fence_token:
                return GatewayToolAuditRecord(
                    tool_name=tool_name,
                    client_id=parsed.claims.client_id,
                    run_id=visibility.run_id,
                    requested_role=parsed.claims.role.value,
                    required_role="unknown",
                    visible=False,
                    reason=f"stale or mismatched fence token: request {req_fence} != session {parsed.claims.fence_token}",
                )
        if token_input is None and isinstance(request, dict) and visibility.role is None:
            visibility = visibility.model_copy(update={"role": "observer"})
        return authorize_gateway_tool_call(
            tool_name,
            visibility,
            session_manager=self.session_manager,
            host_session=token_input,
            require_host_session=self.require_host_session,
            action=action or req_action,
            work_item_id=work_item_id,
            min_fence_token=min_fence_token,
            now=now,
        )

    def validate_tool_call(
        self,
        request: dict[str, Any],
        *,
        min_fence_token: int | None = None,
        now: datetime | None = None,
    ) -> PolicyDecision:
        tool_name = request.get("name") or request.get("tool") or request.get("action") or ""
        try:
            visibility, req_action, req_fence = self._coerce_visibility_request(request)
        except Exception as exc:
            return PolicyDecision(allow=False, denials=[f"invalid tool call request: {exc}"])

        token_input = visibility.host_session
        eff_min_fence = min_fence_token if min_fence_token is not None else visibility.min_fence_token
        effective_action = req_action or GATEWAY_TOOL_ACTIONS.get(tool_name, tool_name)

        if token_input is None:
            if self.require_host_session:
                return PolicyDecision(allow=False, denials=["host session token is required"])
            if tool_name in gateway_tool_registry():
                audit = authorize_gateway_tool_call(tool_name, visibility)
                if not audit.visible:
                    return PolicyDecision(allow=False, denials=[audit.reason])
            return PolicyDecision(allow=True, reasons=["host session omitted and not required"])

        if self.session_manager is None:
            return PolicyDecision(allow=False, denials=["session manager is not configured"])

        parsed, parse_err = _parse_host_session_token(token_input)
        if parsed is None:
            return PolicyDecision(allow=False, denials=[parse_err or "invalid host session token"])

        if req_fence is not None and req_fence != parsed.claims.fence_token:
            return PolicyDecision(
                allow=False,
                denials=[f"stale or mismatched fence token: request {req_fence} != session {parsed.claims.fence_token}"],
            )

        if not _role_matches_claim(visibility.role, parsed.claims.role):
            return PolicyDecision(
                allow=False,
                denials=[f"role mismatch: expected {visibility.role}, got {parsed.claims.role.value}"],
            )

        tool = gateway_tool_registry().get(tool_name)
        if req_action is None and tool is not None and tool.required_role == "observer":
            ok, verify_err = self.session_manager.verify(
                parsed,
                now=now,
                expected_work_item_id=visibility.work_item_id,
                min_fence_token=eff_min_fence,
            )
            if not ok:
                return PolicyDecision(allow=False, denials=[verify_err or "host session verification failed"])
            if visibility.client_id is not None and parsed.claims.client_id != visibility.client_id:
                return PolicyDecision(
                    allow=False,
                    denials=[f"client_id mismatch: expected {visibility.client_id}, got {parsed.claims.client_id}"],
                )
            return PolicyDecision(
                allow=True,
                reasons=[f"host session '{parsed.claims.session_id}' authorized for observer tool '{tool_name}'"],
            )

        return authorize_host_session(
            self.session_manager,
            parsed,
            action=effective_action,
            work_item_id=visibility.work_item_id,
            min_fence_token=eff_min_fence,
            client_id=visibility.client_id,
            now=now,
        )

    def filter_tools(
        self,
        request: GatewayToolVisibilityRequest | dict[str, Any] | None = None,
        *,
        host_session: HostSessionToken | dict[str, Any] | None = None,
        now: datetime | None = None,
    ) -> GatewayToolVisibilityResult:
        visibility, _, _ = self._coerce_visibility_request(request)
        token_input = host_session if host_session is not None else visibility.host_session
        if token_input is None:
            if self.require_host_session:
                audit = tuple(
                    GatewayToolAuditRecord(
                        tool_name=tool.name,
                        client_id=visibility.client_id,
                        run_id=visibility.run_id,
                        requested_role=visibility.role or "observer",
                        required_role=tool.required_role,
                        visible=False,
                        reason="host session token is required",
                    )
                    for tool in _GATEWAY_TOOLS
                )
                return GatewayToolVisibilityResult(tools=(), audit=audit)
            return filter_gateway_tools(visibility)

        visible: list[GatewayToolDefinition] = []
        audit: list[GatewayToolAuditRecord] = []
        for tool in _GATEWAY_TOOLS:
            record = self.authorize_tool_call(
                tool.name,
                visibility,
                host_session=token_input,
                now=now,
            )
            if record.visible:
                visible.append(tool)
            audit.append(record)
        return GatewayToolVisibilityResult(tools=tuple(visible), audit=tuple(audit))


def high_risk_tools() -> tuple[str, ...]:
    return tuple(tool.name for tool in _GATEWAY_TOOLS if tool.high_risk)


def validate_gateway_tool_registry() -> None:
    names = [tool.name for tool in _GATEWAY_TOOLS]
    if len(names) != len(set(names)):
        raise ValueError("gateway tool registry contains duplicate tool names")
    for tool in _GATEWAY_TOOLS:
        if tool.risk == GatewayToolRisk.HIGH and not tool.high_risk:
            raise ValueError(f"high-risk tool {tool.name} must be marked explicitly")
        if tool.input_schema.get("type") != "object":
            raise ValueError(f"tool {tool.name} must use an object input schema")


def _tool_visible(
    tool: GatewayToolDefinition,
    role: str,
    request: GatewayToolVisibilityRequest,
) -> tuple[bool, str]:
    if tool.required_role == "observer":
        return True, "observer tool visible"
    if role != tool.required_role:
        return False, f"requires role {tool.required_role}"
    if tool.high_risk and tool.name not in request.approved_high_risk_tools:
        return False, "high-risk tool requires explicit approval"
    return True, f"role {role} allowed"


def _next_event_sequence(db_path: Path) -> int:
    client = LocalControlPlaneClient.from_db_path(db_path)
    return len(client.store.list_control_plane_events()) + 1


def _tool(
    *,
    name: str,
    description: str,
    properties: dict[str, Any],
    required: list[str] | None = None,
    risk: GatewayToolRisk = GatewayToolRisk.LOW,
    required_role: str = "observer",
    high_risk: bool = False,
) -> GatewayToolDefinition:
    return GatewayToolDefinition(
        name=name,
        description=description,
        input_schema={
            "type": "object",
            "properties": properties,
            **({"required": required} if required else {}),
        },
        risk=risk,
        required_role=required_role,
        high_risk=high_risk,
    )


_GATEWAY_TOOLS: tuple[GatewayToolDefinition, ...] = (
    _tool(
        name="validate_eip",
        description="Validates a VAD EIP (Intent Document) against the schema and validation constraints.",
        properties={"file": {"type": "string", "description": "Path to the EIP file (YAML or JSON)"}},
        required=["file"],
        required_role="observer",
    ),
    _tool(
        name="run_proofs",
        description="Executes the defined proof obligations (e.g. running pytest) to verify requirements.",
        properties={"target": {"type": "string", "description": "Optional pytest target (e.g. tests/test_models.py)"}},
        risk=GatewayToolRisk.MEDIUM,
        required_role="verifier",
    ),
    _tool(
        name="retro_analyze",
        description="Runs retrospective analysis on an evidence bundle, generating learnings and persisting them to MemoryScope.RETROSPECTIVE.",
        properties={"file": {"type": "string", "description": "Path to the evidence bundle JSON or YAML file"}},
        required=["file"],
        risk=GatewayToolRisk.MEDIUM,
        required_role="auditor",
    ),
    _tool(
        name="query_retrospective",
        description="Queries historical retrospective learnings from the VAD memory store.",
        properties={},
        required_role="observer",
    ),
    _tool(
        name="submit_retrospective",
        description="Manually records a learning back into the retrospective memory scope.",
        properties={"learning": {"type": "string", "description": "A clear, actionable learning or rule to record"}},
        required=["learning"],
        risk=GatewayToolRisk.MEDIUM,
        required_role="auditor",
    ),
    _tool(
        name="repo_assess",
        description="Assess a local repository without mutation.",
        properties={"path": {"type": "string"}},
        required=["path"],
        required_role="observer",
    ),
    _tool(
        name="repo_patch",
        description="Apply an explicit unified diff to a repo, run proofs, and roll back on denial/failure.",
        properties={
            "path": {"type": "string"},
            "eip_file": {"type": "string"},
            "proof_plan_file": {"type": "string"},
            "patch_file": {"type": "string"},
            "allow_dirty": {"type": "boolean"},
            "approve_dependencies": {"type": "boolean"},
        },
        required=["path", "eip_file", "proof_plan_file", "patch_file"],
        risk=GatewayToolRisk.HIGH,
        required_role="builder",
        high_risk=True,
    ),
    _tool(
        name="repo_run",
        description="Run bounded repository automation using an explicit patch file.",
        properties={
            "path": {"type": "string"},
            "eip_file": {"type": "string"},
            "proof_plan_file": {"type": "string"},
            "patch_file": {"type": "string"},
            "allow_dirty": {"type": "boolean"},
            "approve_dependencies": {"type": "boolean"},
        },
        required=["path", "eip_file", "proof_plan_file", "patch_file"],
        risk=GatewayToolRisk.HIGH,
        required_role="builder",
        high_risk=True,
    ),
    _tool(
        name="sign_verify",
        description="Verify a signed evidence artifact with a local development secret file.",
        properties={"signed_file": {"type": "string"}, "secret_file": {"type": "string"}},
        required=["signed_file", "secret_file"],
        risk=GatewayToolRisk.HIGH,
        required_role="release_guardian",
        high_risk=True,
    ),
    _tool(
        name="evidence_inspect",
        description="Inspect evidence and return its deterministic hash.",
        properties={"file": {"type": "string"}},
        required=["file"],
        required_role="observer",
    ),
    _tool(
        name="provider_inventory",
        description="Return provider inventory for fake or OpenAI adapters without live calls.",
        properties={"provider": {"type": "string", "enum": ["fake", "openai"]}},
        required=["provider"],
        required_role="observer",
    ),
    _tool(
        name="provider_test",
        description="Run an offline provider completion contract test.",
        properties={"prompt": {"type": "string"}, "model": {"type": "string"}},
        required=["prompt"],
        risk=GatewayToolRisk.MEDIUM,
        required_role="verifier",
    ),
    _tool(
        name="swarm_status",
        description="Read persisted swarm state and reconstruct status.",
        properties={"state": {"type": "string"}},
        required=["state"],
        required_role="observer",
    ),
    _tool(
        name="work_intake_discover",
        description="Discovers available or queued work items matching agent role and capability requirements.",
        properties={
            "role": {"type": "string", "default": "developer"},
            "capability": {"type": "string"},
            "limit": {"type": "integer", "default": 10},
        },
        required_role="developer",
        risk=GatewayToolRisk.LOW,
    ),
    _tool(
        name="work_intake_claim",
        description="Acquires an execution lease on a queued work item for the agent.",
        properties={
            "work_item_id": {"type": "string"},
            "client_id": {"type": "string"},
            "lease_duration_seconds": {"type": "integer", "default": 600},
        },
        required=["work_item_id", "client_id"],
        required_role="developer",
        risk=GatewayToolRisk.MEDIUM,
    ),
    _tool(
        name="work_intake_report_progress",
        description="Reports interim progress, milestone status, or completion percentage on a claimed work item.",
        properties={
            "work_item_id": {"type": "string"},
            "lease_id": {"type": "string"},
            "message": {"type": "string"},
            "percent_complete": {"type": "integer"},
        },
        required=["work_item_id", "lease_id", "message"],
        required_role="developer",
        risk=GatewayToolRisk.LOW,
    ),
    _tool(
        name="work_intake_submit_proof",
        description="Submits verification proof digest and test summary, transitioning work item to verifying state.",
        properties={
            "work_item_id": {"type": "string"},
            "lease_id": {"type": "string"},
            "evidence_digest": {"type": "string"},
            "test_summary": {"type": "string"},
            "commit_sha": {"type": "string"},
        },
        required=["work_item_id", "lease_id", "evidence_digest", "test_summary"],
        required_role="developer",
        risk=GatewayToolRisk.HIGH,
        high_risk=True,
    ),
    _tool(
        name="work_intake_escalate",
        description="Escalates a blocker or requests human intervention on a claimed work item.",
        properties={
            "work_item_id": {"type": "string"},
            "lease_id": {"type": "string"},
            "reason": {"type": "string"},
        },
        required=["work_item_id", "lease_id", "reason"],
        required_role="developer",
        risk=GatewayToolRisk.HIGH,
        high_risk=True,
    ),
)


validate_gateway_tool_registry()
