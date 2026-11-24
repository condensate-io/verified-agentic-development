from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict

from vad.control_plane.mcp_gateway import (
    GatewayToolDefinition,
    GatewayToolEventPolicy,
    GatewayToolRisk,
    MCPGateway,
    _parse_host_session_token,
    _role_matches_claim,
    authorize_host_session,
)
from vad.control_plane.sessions import HostSessionManager, HostSessionToken
from vad.policy.decisions import PolicyDecision


def get_work_intake_tool_definitions() -> Dict[str, GatewayToolDefinition]:
    """Returns the standardized MCP tool definitions for governed work intake."""
    return {
        "work_intake_discover": GatewayToolDefinition(
            name="work_intake_discover",
            description="Discovers available or queued work items matching agent role and capability requirements.",
            risk=GatewayToolRisk.LOW,
            required_role="developer",
            high_risk=False,
            input_schema={
                "type": "object",
                "properties": {
                    "role": {
                        "type": "string",
                        "description": "Agent role to match (e.g. 'developer', 'qa', 'architect')",
                        "default": "developer",
                    },
                    "capability": {
                        "type": "string",
                        "description": "Optional specific capability required for work items",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of items to return",
                        "default": 10,
                    },
                },
            },
        ),
        "work_intake_claim": GatewayToolDefinition(
            name="work_intake_claim",
            description="Acquires an execution lease on a queued work item for the agent.",
            risk=GatewayToolRisk.MEDIUM,
            required_role="developer",
            high_risk=False,
            input_schema={
                "type": "object",
                "properties": {
                    "work_item_id": {
                        "type": "string",
                        "description": "Identifier of the work item to claim",
                    },
                    "client_id": {
                        "type": "string",
                        "description": "Agent client identifier",
                    },
                    "lease_duration_seconds": {
                        "type": "integer",
                        "description": "Lease duration in seconds (default: 600)",
                        "default": 600,
                    },
                },
                "required": ["work_item_id", "client_id"],
            },
        ),
        "work_intake_report_progress": GatewayToolDefinition(
            name="work_intake_report_progress",
            description="Reports interim progress, milestone status, or completion percentage on a claimed work item.",
            risk=GatewayToolRisk.LOW,
            required_role="developer",
            high_risk=False,
            input_schema={
                "type": "object",
                "properties": {
                    "work_item_id": {
                        "type": "string",
                        "description": "Identifier of the claimed work item",
                    },
                    "lease_id": {
                        "type": "string",
                        "description": "Active lease identifier for the work item",
                    },
                    "message": {
                        "type": "string",
                        "description": "Progress note or status explanation",
                    },
                    "percent_complete": {
                        "type": "integer",
                        "description": "Estimated percent complete (0-100)",
                    },
                },
                "required": ["work_item_id", "lease_id", "message"],
            },
        ),
        "work_intake_submit_proof": GatewayToolDefinition(
            name="work_intake_submit_proof",
            description="Submits verification proof digest and test summary, transitioning work item to verifying state.",
            risk=GatewayToolRisk.HIGH,
            required_role="developer",
            high_risk=True,
            input_schema={
                "type": "object",
                "properties": {
                    "work_item_id": {
                        "type": "string",
                        "description": "Identifier of the work item",
                    },
                    "lease_id": {
                        "type": "string",
                        "description": "Active lease identifier",
                    },
                    "evidence_digest": {
                        "type": "string",
                        "description": "64-character SHA-256 evidence digest",
                    },
                    "test_summary": {
                        "type": "string",
                        "description": "Summary of executed tests and verification outcomes",
                    },
                    "commit_sha": {
                        "type": "string",
                        "description": "Optional Git commit SHA associated with the proof",
                    },
                },
                "required": ["work_item_id", "lease_id", "evidence_digest", "test_summary"],
            },
        ),
        "work_intake_escalate": GatewayToolDefinition(
            name="work_intake_escalate",
            description="Escalates a blocker or requests human intervention on a claimed work item.",
            risk=GatewayToolRisk.HIGH,
            required_role="developer",
            high_risk=True,
            input_schema={
                "type": "object",
                "properties": {
                    "work_item_id": {
                        "type": "string",
                        "description": "Identifier of the work item",
                    },
                    "lease_id": {
                        "type": "string",
                        "description": "Active lease identifier",
                    },
                    "reason": {
                        "type": "string",
                        "description": "Explanation of the blocking issue or required human decision",
                    },
                },
                "required": ["work_item_id", "lease_id", "reason"],
            },
        ),
    }


_WORK_TOOL_TO_ACTION: dict[str, str] = {
    "work_intake_discover": "discover_work",
    "work_intake_claim": "claim_work",
    "work_intake_report_progress": "report_progress",
    "work_intake_submit_proof": "submit_builder_claim",
    "work_intake_escalate": "escalate",
}

_GOVERNED_NON_DB_ACTIONS: frozenset[str] = frozenset(
    {
        "submit_diff_proposal",
        "submit_verification_result",
        "approve_release",
        "reject_release",
        "promote_lesson",
        "inspect_audit",
        "create_work",
        "assign_work",
        "recover_stale",
    }
)


def _resolve_store_from_params(params: Dict[str, Any]) -> Any | None:
    from vad.server.db.store import ServerStore

    if "db_path" in params and params["db_path"]:
        path_val = params["db_path"]
        return ServerStore(Path(path_val) if isinstance(path_val, str) else path_val)
    env_db = os.environ.get("VAD_SERVER_DB")
    if env_db:
        return ServerStore(Path(env_db))
    return None


class WorkIntakeToolHandler:
    """Governed MCP work-intake tool handler enforcing scoped host sessions and fence tokens."""

    def __init__(
        self,
        *,
        session_manager: HostSessionManager | None = None,
        require_host_session: bool = False,
        gateway: MCPGateway | None = None,
        extra_handlers: dict[str, Callable[[Dict[str, Any]], Dict[str, Any]]] | None = None,
    ) -> None:
        self.session_manager = session_manager
        self.require_host_session = require_host_session
        self.gateway = gateway or MCPGateway(
            session_manager=session_manager,
            require_host_session=require_host_session,
        )
        self.extra_handlers = dict(extra_handlers or {})

    def _resolve_min_fence_token(
        self,
        params: Dict[str, Any],
        explicit_min_fence: int | None,
    ) -> tuple[int | None, str | None]:
        if explicit_min_fence is not None:
            return explicit_min_fence, None
        if params.get("min_fence_token") is not None:
            try:
                return int(params["min_fence_token"]), None
            except (TypeError, ValueError):
                return None, "invalid min_fence_token"
        work_item_id = params.get("work_item_id")
        if work_item_id:
            store = _resolve_store_from_params(params)
            if store is not None:
                try:
                    lease = store.load_task_lease(work_item_id)
                    return lease.fence_token, None
                except KeyError:
                    return None, None
        return None, None

    def authorize_call(
        self,
        tool_or_action: str,
        params: Dict[str, Any] | None = None,
        *,
        host_session: HostSessionToken | dict[str, Any] | None = None,
        min_fence_token: int | None = None,
        now: datetime | None = None,
    ) -> PolicyDecision:
        call_params = dict(params or {})
        token_input = host_session if host_session is not None else call_params.get("host_session")

        if token_input is None:
            if self.require_host_session:
                return PolicyDecision(allow=False, denials=["host session token is required"])
            return PolicyDecision(allow=True, reasons=["host session omitted and not required"])

        if self.session_manager is None:
            return PolicyDecision(allow=False, denials=["session manager is not configured"])

        parsed, parse_err = _parse_host_session_token(token_input)
        if parsed is None:
            return PolicyDecision(allow=False, denials=[parse_err or "invalid host session token"])

        if call_params.get("fence_token") is not None:
            try:
                req_fence = int(call_params["fence_token"])
            except (TypeError, ValueError):
                return PolicyDecision(allow=False, denials=["invalid fence_token"])
            if req_fence != parsed.claims.fence_token:
                return PolicyDecision(
                    allow=False,
                    denials=[
                        f"stale or mismatched fence token: request {req_fence} != session {parsed.claims.fence_token}"
                    ],
                )

        eff_min_fence, fence_err = self._resolve_min_fence_token(call_params, min_fence_token)
        if fence_err is not None:
            return PolicyDecision(allow=False, denials=[fence_err])

        req_role = call_params.get("role")
        if not _role_matches_claim(req_role, parsed.claims.role):
            return PolicyDecision(
                allow=False,
                denials=[f"role mismatch: expected {req_role}, got {parsed.claims.role.value}"],
            )

        action = _WORK_TOOL_TO_ACTION.get(tool_or_action, tool_or_action)
        return authorize_host_session(
            self.session_manager,
            parsed,
            action=action,
            work_item_id=call_params.get("work_item_id"),
            min_fence_token=eff_min_fence,
            client_id=call_params.get("client_id"),
            now=now,
        )

    def handle(
        self,
        tool_name: str,
        params: Dict[str, Any] | None = None,
        *,
        host_session: HostSessionToken | dict[str, Any] | None = None,
        min_fence_token: int | None = None,
        now: datetime | None = None,
    ) -> Dict[str, Any]:
        call_params = dict(params or {})
        decision = self.authorize_call(
            tool_name,
            call_params,
            host_session=host_session,
            min_fence_token=min_fence_token,
            now=now,
        )
        if not decision.allow:
            return {
                "content": [
                    {
                        "type": "text",
                        "text": f"Host session policy denied ({tool_name}): {'; '.join(decision.denials)}",
                    }
                ],
                "isError": True,
                "policy_decision": decision.model_dump(mode="json"),
            }

        token_input = host_session if host_session is not None else call_params.get("host_session")
        if token_input is not None:
            parsed, _ = _parse_host_session_token(token_input)
            if parsed is not None and "client_id" not in call_params:
                call_params["client_id"] = parsed.claims.client_id

        if tool_name in self.extra_handlers:
            result = dict(self.extra_handlers[tool_name](call_params))
            result["policy_decision"] = decision.model_dump(mode="json")
            return result

        from vad.adapters.mcp_work_intake import (
            handle_work_intake_claim,
            handle_work_intake_discover,
            handle_work_intake_escalate,
            handle_work_intake_report_progress,
            handle_work_intake_submit_proof,
        )

        dispatch = {
            "work_intake_discover": handle_work_intake_discover,
            "discover_work": handle_work_intake_discover,
            "work_intake_claim": handle_work_intake_claim,
            "claim_work": handle_work_intake_claim,
            "work_intake_report_progress": handle_work_intake_report_progress,
            "report_progress": handle_work_intake_report_progress,
            "work_intake_submit_proof": handle_work_intake_submit_proof,
            "submit_builder_claim": handle_work_intake_submit_proof,
            "work_intake_escalate": handle_work_intake_escalate,
            "escalate": handle_work_intake_escalate,
        }
        handler = dispatch.get(tool_name)
        if handler is not None:
            result = dict(handler(call_params))
            result["policy_decision"] = decision.model_dump(mode="json")
            return result

        if token_input is not None and tool_name in _GOVERNED_NON_DB_ACTIONS:
            return {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps({"status": "authorized", "action": tool_name}, indent=2),
                    }
                ],
                "isError": False,
                "policy_decision": decision.model_dump(mode="json"),
            }

        return {
            "content": [{"type": "text", "text": f"Unknown work-intake tool: {tool_name}"}],
            "isError": True,
            "policy_decision": decision.model_dump(mode="json"),
        }

    def discover(self, params: Dict[str, Any] | None = None, **kwargs: Any) -> Dict[str, Any]:
        return self.handle("work_intake_discover", params, **kwargs)

    def claim(self, params: Dict[str, Any] | None = None, **kwargs: Any) -> Dict[str, Any]:
        return self.handle("work_intake_claim", params, **kwargs)

    def report_progress(self, params: Dict[str, Any] | None = None, **kwargs: Any) -> Dict[str, Any]:
        return self.handle("work_intake_report_progress", params, **kwargs)

    def submit_proof(self, params: Dict[str, Any] | None = None, **kwargs: Any) -> Dict[str, Any]:
        return self.handle("work_intake_submit_proof", params, **kwargs)

    def escalate(self, params: Dict[str, Any] | None = None, **kwargs: Any) -> Dict[str, Any]:
        return self.handle("work_intake_escalate", params, **kwargs)


def authorize_work_intake_tool_call(
    tool_or_action: str,
    params: Dict[str, Any] | None = None,
    *,
    session_manager: HostSessionManager | None = None,
    require_host_session: bool = False,
    host_session: HostSessionToken | dict[str, Any] | None = None,
    min_fence_token: int | None = None,
    now: datetime | None = None,
) -> PolicyDecision:
    """Authorize a work-intake MCP tool call with optional scoped HostSessionToken validation."""
    handler = WorkIntakeToolHandler(
        session_manager=session_manager,
        require_host_session=require_host_session,
    )
    return handler.authorize_call(
        tool_or_action,
        params,
        host_session=host_session,
        min_fence_token=min_fence_token,
        now=now,
    )


def handle_work_intake_tool_call(
    tool_name: str,
    params: Dict[str, Any] | None = None,
    *,
    session_manager: HostSessionManager | None = None,
    require_host_session: bool = False,
    host_session: HostSessionToken | dict[str, Any] | None = None,
    min_fence_token: int | None = None,
    now: datetime | None = None,
) -> Dict[str, Any]:
    """Execute a work-intake MCP tool call with optional scoped HostSessionToken enforcement."""
    handler = WorkIntakeToolHandler(
        session_manager=session_manager,
        require_host_session=require_host_session,
    )
    return handler.handle(
        tool_name,
        params,
        host_session=host_session,
        min_fence_token=min_fence_token,
        now=now,
    )
