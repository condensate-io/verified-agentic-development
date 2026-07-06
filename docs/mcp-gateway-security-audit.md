# MCP Gateway Security Audit

This audit covers the local MCP gateway used by `vad mcp run` and the optional
local `POST /mcp` route. The gateway is a local/offline bridge, not a remote MCP gateway,
hosted VAD SaaS endpoint, managed tenant boundary, or production authorization
service.

## Scope

Audited surfaces:

- stdio MCP server: `vad mcp run`;
- local HTTP MCP route: `POST /mcp`;
- governed registry: `vad.control_plane.mcp_gateway.gateway_tool_registry`;
- adapter dispatch: `vad.adapters.mcp.TOOL_HANDLERS`, `tools/list`, and
  `tools/call`;
- local control-plane event attribution through `control_plane_db`,
  `client_id`, `actor_id`, `role`, `run_id`, and `task_id`.

Out of scope and not implemented: remote MCP hosting, browser-facing tool
execution, cloud policy distribution, marketplace approval, automatic client
trust, production key management, and live provider credentials.

## Tool Exposure Defaults

Unknown clients and clients with no role default to the `observer` role. The
observer-visible tool set is intentionally read-only and limited to:

- `validate_eip`
- `query_retrospective`
- `repo_assess`
- `evidence_inspect`
- `provider_inventory`
- `swarm_status`

`tools/list` returns both visible tools and `tool_visibility_audit` records for
hidden tools. This makes denials reviewable instead of silently omitting
policy-sensitive tools.

The registry is the source of truth for MCP exposure. Tests require
`gateway_tool_registry`, `vad.adapters.mcp.TOOL_HANDLERS`, and the default
stdio `TOOLS` list to stay aligned, so new handlers cannot become visible
without role, risk, schema, and event-policy metadata.

## Dangerous Tool Denial

The current high-risk tools are:

- `repo_patch`
- `repo_run`
- `sign_verify`

High-risk tools remain hidden unless the request has the required role and the
tool name appears in `approved_high_risk_tools` for that request. Required roles
are:

- `repo_patch`: `builder`
- `repo_run`: `builder`
- `sign_verify`: `release_guardian`

`tools/call` reuses the same visibility policy before handler execution. A
client cannot invoke a hidden tool by calling it directly by name. Unapproved
high-risk calls return a denial such as `high-risk tool requires explicit approval`,
and denied calls can emit blocked `tool_call_finished` evidence when
control-plane attribution is supplied.

Medium-risk tools remain role-bound:

- `run_proofs` and `provider_test` require `verifier`;
- `retro_analyze` and `submit_retrospective` require `auditor`.

## Path And Secret Handling

Gateway identity fields reject path and control separators. This applies to
`client_id`, `run_id`, `role`, `actor_id`, `task_id`, and
`approved_high_risk_tools`, preventing identifiers from being reused as unsafe
paths or multi-line log control values.

Tool started-event summaries redact sensitive argument keys recursively before
persistence. Redacted key markers include:

- `secret`
- `token`
- `password`
- `api_key`
- `private_key`
- `key`

This protects paths such as `secret_file`, nested metadata such as `api_key`,
and list entries such as `token` before the gateway writes replayable event
summaries. The broader workspace write boundary remains enforced by repository
patch and workspace sandbox tests; the MCP gateway audit does not expand write
authority.

## Client Identity Evidence

MCP calls may include `control_plane_db`, `client_id`, `actor_id`, `role`,
`run_id`, and `task_id`. When present, the gateway emits local control-plane
events with:

- `tool_call_started` before handler execution;
- `tool_call_finished` after success or failure;
- blocked `tool_call_finished` evidence for denied tool calls;
- `client_id`, `actor`, `role`, `run_id`, and `task_id` copied into the event
  envelope;
- `evidence_digest` linked when a handler result carries a digest.

Generated package smoke tests cover generic MCP, Claude Code, Codex, VS Code,
Windsurf, OpenCode, and Antigravity/generic fallback clients, proving local
client attribution reaches the SQLite-backed event ledger without live network
services.

## Verification Matrix

| Requirement | Evidence |
| --- | --- |
| Tool exposure defaults | `tests/test_mcp_gateway_registry.py::test_gateway_tool_filter_defaults_unknown_clients_to_safe_observer_tools` |
| Registry/handler drift prevention | `tests/test_mcp_gateway_registry.py::test_gateway_tool_registry_covers_current_mcp_handlers_and_tool_list` |
| Dangerous tool denial | `tests/test_mcp_gateway_registry.py::test_gateway_tool_call_authorization_reuses_visibility_policy` and `tests/test_server_api.py::test_mcp_http_endpoint_denies_unapproved_high_risk_tool` |
| Path/control separator rejection | `tests/test_mcp_gateway_registry.py::test_gateway_security_audit_rejects_path_and_control_separators_in_identity_fields` |
| Secret redaction | `tests/test_mcp_gateway_registry.py::test_gateway_tool_argument_redaction_recurses_through_sensitive_keys` |
| Client identity evidence | package smoke tests plus `tests/test_mcp_gateway_registry.py::test_gateway_security_audit_records_client_identity_in_tool_events` |
| Local-only HTTP MCP route | `tests/test_server_api.py::test_mcp_http_route_is_local_only` |

## Residual Limitations

- The HTTP MCP route is local-only and JSON-RPC shaped; streamable HTTP/SSE MCP
  transport is not implemented.
- Approval is request-scoped through `approved_high_risk_tools`; there is no
  long-lived production grant store.
- The gateway is not a production authorization service. It is a local evidence
  and policy boundary for the current local Level 4 distribution.
