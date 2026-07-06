from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator

from vad.control_plane.events import ControlPlaneEvent, ControlPlaneEventKind, ControlPlaneEventStatus
from vad.control_plane.sdk import LocalControlPlaneClient


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

    @field_validator("client_id", "run_id", "role")
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


def authorize_gateway_tool_call(tool_name: str, request: GatewayToolVisibilityRequest | None = None) -> GatewayToolAuditRecord:
    visibility = request or GatewayToolVisibilityRequest(role="observer")
    registry = gateway_tool_registry()
    tool = registry.get(tool_name)
    role = visibility.role or "observer"
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
)


validate_gateway_tool_registry()
