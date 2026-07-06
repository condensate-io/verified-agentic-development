from pathlib import Path

from vad.adapters.mcp import TOOL_HANDLERS, TOOLS, list_tools_for_client
from vad.control_plane.events import ControlPlaneEventKind
from vad.control_plane.mcp_gateway import (
    GatewayToolRisk,
    GatewayToolVisibilityRequest,
    McpToolCallRecorder,
    authorize_gateway_tool_call,
    filter_gateway_tools,
    gateway_tool_registry,
    high_risk_tools,
    redact_tool_arguments,
)
from vad.server.db.store import ServerStore


DOCS = Path(__file__).resolve().parents[1] / "docs"


def test_gateway_tool_registry_covers_current_mcp_handlers_and_tool_list():
    registry = gateway_tool_registry()
    registry_names = set(registry)
    handler_names = set(TOOL_HANDLERS)
    listed_names = {tool["name"] for tool in TOOLS}

    assert registry_names == handler_names
    assert listed_names == {name for name, tool in registry.items() if tool.required_role == "observer"}


def test_gateway_tool_registry_records_schema_role_risk_and_event_policy_for_every_tool():
    registry = gateway_tool_registry()

    for tool in registry.values():
        assert tool.input_schema["type"] == "object"
        assert isinstance(tool.required_role, str)
        assert tool.required_role
        assert tool.event_policy.emits_started is True
        assert tool.event_policy.emits_finished is True
        assert tool.event_policy.emits_failed is True
        assert tool.event_policy.started_kind == ControlPlaneEventKind.TOOL_CALL_STARTED
        assert tool.event_policy.finished_kind == ControlPlaneEventKind.TOOL_CALL_FINISHED


def test_gateway_tool_registry_marks_high_risk_tools_explicitly():
    registry = gateway_tool_registry()

    assert set(high_risk_tools()) == {"repo_patch", "repo_run", "sign_verify"}
    for name in high_risk_tools():
        assert registry[name].risk == GatewayToolRisk.HIGH
        assert registry[name].high_risk is True


def test_gateway_tool_registry_roles_match_current_local_policy_expectations():
    registry = gateway_tool_registry()

    assert registry["repo_assess"].required_role == "observer"
    assert registry["repo_patch"].required_role == "builder"
    assert registry["repo_run"].required_role == "builder"
    assert registry["run_proofs"].required_role == "verifier"
    assert registry["retro_analyze"].required_role == "auditor"
    assert registry["sign_verify"].required_role == "release_guardian"


def test_gateway_tool_filter_defaults_unknown_clients_to_safe_observer_tools():
    result = filter_gateway_tools(GatewayToolVisibilityRequest(client_id="unknown-client"))
    names = {tool.name for tool in result.tools}

    assert names == {
        "validate_eip",
        "query_retrospective",
        "repo_assess",
        "evidence_inspect",
        "provider_inventory",
        "swarm_status",
    }
    hidden = {record.tool_name: record for record in result.audit if not record.visible}
    assert hidden["repo_patch"].reason == "requires role builder"
    assert hidden["run_proofs"].reason == "requires role verifier"


def test_gateway_tool_filter_uses_role_and_high_risk_approval():
    builder_without_approval = filter_gateway_tools(GatewayToolVisibilityRequest(
        client_id="codex-local",
        role="builder",
    ))
    builder_with_approval = filter_gateway_tools(GatewayToolVisibilityRequest(
        client_id="codex-local",
        run_id="run-1",
        role="builder",
        approved_high_risk_tools=("repo_patch",),
    ))

    assert "repo_patch" not in {tool.name for tool in builder_without_approval.tools}
    assert "repo_patch" in {tool.name for tool in builder_with_approval.tools}
    denied = next(record for record in builder_without_approval.audit if record.tool_name == "repo_patch")
    allowed = next(record for record in builder_with_approval.audit if record.tool_name == "repo_patch")
    assert denied.reason == "high-risk tool requires explicit approval"
    assert allowed.visible is True
    assert allowed.run_id == "run-1"


def test_gateway_tool_call_authorization_reuses_visibility_policy():
    denied = authorize_gateway_tool_call("repo_patch", GatewayToolVisibilityRequest(
        client_id="codex-local",
        role="builder",
    ))
    allowed = authorize_gateway_tool_call("repo_patch", GatewayToolVisibilityRequest(
        client_id="codex-local",
        role="builder",
        approved_high_risk_tools=("repo_patch",),
    ))

    assert denied.visible is False
    assert denied.reason == "high-risk tool requires explicit approval"
    assert allowed.visible is True
    assert allowed.required_role == "builder"


def test_mcp_tool_list_uses_filter_and_returns_audit_records():
    payload = list_tools_for_client({"client_id": "vscode", "role": "verifier"})
    names = {tool["name"] for tool in payload["tools"]}
    audit = {record["tool_name"]: record for record in payload["tool_visibility_audit"]}

    assert "run_proofs" in names
    assert "provider_test" in names
    assert "repo_patch" not in names
    assert audit["repo_patch"]["visible"] is False
    assert audit["repo_patch"]["reason"] == "requires role builder"


def test_gateway_tool_argument_redaction_recurses_through_sensitive_keys():
    redacted = redact_tool_arguments({
        "file": "evidence.json",
        "secret_file": "/tmp/dev.secret",
        "metadata": {
            "api_key": "sk-test",
            "note": "safe",
        },
        "items": [
            {"token": "abc", "name": "kept"},
        ],
    })

    assert redacted["file"] == "evidence.json"
    assert redacted["secret_file"] == "[REDACTED]"
    assert redacted["metadata"]["api_key"] == "[REDACTED]"
    assert redacted["metadata"]["note"] == "safe"
    assert redacted["items"][0]["token"] == "[REDACTED]"
    assert redacted["items"][0]["name"] == "kept"


def test_gateway_security_audit_doc_covers_required_boundaries():
    text = (DOCS / "mcp-gateway-security-audit.md").read_text(encoding="utf-8")

    for expected in [
        "## Tool Exposure Defaults",
        "Unknown clients and clients with no role default to the `observer` role",
        "tool_visibility_audit",
        "## Dangerous Tool Denial",
        "`repo_patch`",
        "`repo_run`",
        "`sign_verify`",
        "high-risk tool requires explicit approval",
        "## Path And Secret Handling",
        "`client_id`",
        "`approved_high_risk_tools`",
        "`secret`",
        "`token`",
        "`password`",
        "`api_key`",
        "`private_key`",
        "## Client Identity Evidence",
        "`tool_call_started`",
        "`tool_call_finished`",
        "`control_plane_db`",
        "`actor_id`",
        "`task_id`",
        "not a remote MCP gateway",
        "Local-only HTTP MCP route",
    ]:
        assert expected in text


def test_gateway_security_audit_rejects_path_and_control_separators_in_identity_fields():
    unsafe_requests = [
        {"client_id": "../codex"},
        {"run_id": "run/one"},
        {"role": "builder\nrelease_guardian"},
        {"approved_high_risk_tools": ("repo_patch/escape",)},
    ]

    for payload in unsafe_requests:
        try:
            GatewayToolVisibilityRequest(**payload)
        except ValueError as exc:
            assert "separator" in str(exc)
        else:
            raise AssertionError(f"unsafe gateway visibility request accepted: {payload}")

    try:
        McpToolCallRecorder(db_path=DOCS / "unused.sqlite3", client_id="codex/local", actor_id="codex", role="builder")
    except ValueError as exc:
        assert "separator" in str(exc)
    else:
        raise AssertionError("unsafe MCP recorder identity accepted")


def test_gateway_security_audit_records_client_identity_in_tool_events(tmp_path):
    db_path = tmp_path / "vad.sqlite3"
    recorder = McpToolCallRecorder(
        db_path=db_path,
        client_id="codex-local",
        actor_id="codex-builder",
        role="builder",
        run_id="run-1",
        task_id="task-1",
    )

    recorder.record_started(tool_name="repo_patch", args={"secret_file": "/tmp/raw-secret", "path": "."})
    recorder.record_denied(tool_name="repo_patch", reason="high-risk tool requires explicit approval")
    digest = "a" * 64
    recorder.record_finished(tool_name="repo_assess", result={"mcp_attribution": {"evidence_digest": digest}})

    events = ServerStore(db_path).list_control_plane_events()
    assert [event.kind for event in events] == [
        ControlPlaneEventKind.TOOL_CALL_STARTED,
        ControlPlaneEventKind.TOOL_CALL_FINISHED,
        ControlPlaneEventKind.TOOL_CALL_FINISHED,
    ]
    assert {event.client_id for event in events} == {"codex-local"}
    assert {event.actor for event in events} == {"codex-builder"}
    assert {event.role for event in events} == {"builder"}
    assert {event.run_id for event in events} == {"run-1"}
    assert {event.task_id for event in events} == {"task-1"}
    assert "raw-secret" not in " ".join(event.summary for event in events)
    assert events[1].status.value == "blocked"
    assert events[2].evidence_digest == digest
