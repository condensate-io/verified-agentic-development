# Local Level 4 Control-Plane Architecture

This document records the current implemented local Level 4 Agentic OS boundary. It separates implemented local behavior, remaining local hardening, and future cloud/SaaS work so the architecture can be read without overclaiming hosted or production capabilities.

## Implemented Local Level 4 Boundary

The current serving path is a local control-plane demonstrator over SQLite, local files, stdlib HTTP, and stdio MCP. It is Level 4-local in the sense that it coordinates multi-client identity, event replay, task leases, governed MCP visibility, plugin package metadata, and dashboard inspection without a cloud service.

- `vad control-plane serve` starts the localhost control-plane server with lifecycle and readiness semantics.
- `vad local-os demo` starts the local OS demonstrator, seeds deterministic multi-client simulator state, and serves dashboard/replay from `.vad/local-os`.
- `vad ui serve` remains a local operator UI/API entrypoint and compatibility surface for Level 3/demo data.
- `vad.server.serve` is the lower-level Python module used by the CLI and container services. It exposes `prepare_ui_server` and `serve_ui`.
- `vad.server.app` owns the stdlib HTTP server, JSON API routing, static UI serving, event ingestion, client/lease APIs, dashboard replay, plugin status, run/evidence reads, and approval action routing.
- `vad.server.db.store` owns local SQLite persistence for run evidence, approval events, dashboard compatibility data, control-plane events, client manifests/runtime state, task leases, and schema migrations.
- `compose.yaml` runs both the local `vad-ui` service and the opt-in `level4` `vad-control-plane` profile with deterministic local data and `VAD_LIVE_SERVICES=disabled`.
- `vad.adapters.mcp` exposes MCP tools through stdio and delegates tool metadata/visibility to the governed MCP gateway registry.
- Swarm, deployment, provider, and signing behavior remain deterministic local reference paths; control-plane events and dashboard replay can represent their local activity, but live production orchestration remains out of scope.

Implemented local Level 4 surfaces include:

- lifecycle/readiness model and local serving command;
- local config validation with localhost defaults and explicit non-local bind opt-in;
- append-only event ledger, ingestion API, replay projection, and replay route;
- client manifest registry, heartbeats, stale scans, task leases, and release-guardian approval checks;
- durable work-item model, SQLite persistence, explicit state transitions,
  scheduler decisions, and replayable work-item events for queued orchestration
  work;
- role-aware MCP gateway visibility for stdio and local HTTP JSON-RPC;
- local SDK and CLI event emission;
- plugin manifest schema, artifact digest/signature evidence, installer dry-run, plugin status seed, artifact audit, reproducible artifact builder, publication decision records, installed-artifact smoke tests, and local install guidance;
- multi-client simulator and role-separation fixtures covering Codex, Antigravity, Claude Code, VS Code, Windsurf, Cursor, OpenCode, and Generic MCP/A2A.

## Remaining Local Hardening

The following remain optional local hardening work, not future cloud work:

- optional streamable local HTTP/SSE MCP transport when a client package needs it and can keep the local-only boundary.

Implemented in the current local reference:

- durable run/task state projection in `run_task_states`, synced from work-item transitions;
- automatic stale-client recovery reassignment through `POST /clients/stale-scan` with `auto_reassign`;
- event-derived plugin dashboard status merged from persisted inventory and control-plane events;
- diff proposal persistence and replay through `/diff-proposals` API/CLI surfaces;
- durable operator intent records through `/operator-intents` for recurring high-risk grants;
- governance dashboard summary derived from work-item MEES and token budgets.

## Future Cloud Scope

The following are not implemented in the current local reference architecture:

- hosted VAD SaaS or managed tenancy;
- cloud dashboard or remote team event aggregation;
- cloud-hosted MCP gateway or remote MCP marketplace owned by VAD;
- managed plugin marketplace publication or automatic marketplace acceptance;
- live production deployment providers in default flows;
- paid model calls or credential-backed providers in default tests;
- production key management, KMS, Sigstore, or hardware-backed signing.

Any future cloud or SaaS plan must be introduced by a later plan/tracker item with opt-in live tests plus deterministic offline contract tests.

## Default Binding Audit

The operator-facing CLI path is local by default:

```text
vad ui serve --host defaults to 127.0.0.1
```

The lower-level module currently defaults to `0.0.0.0` because it is also used inside the Docker service. That default must not be treated as the future control-plane default. For Level 4, any operator-facing control-plane command must bind to `127.0.0.1` by default. Any non-local bind must require an explicit flag, warning, and test coverage.

The Docker service binds `0.0.0.0` inside the container and maps `8080:8080` for local smoke testing. This is acceptable for the existing local demo service but must be revisited when a Level 4 control-plane profile is added.

## Existing APIs

Implemented API routes:

- `GET /health`
- `GET /runs`
- `GET /dashboard`
- `GET /dashboard/replay`
- `GET /runs/{run_id}`
- `GET /runs/{run_id}/evidence`
- `GET /runs/{run_id}/approvals`
- `POST /actions/approve`

The approval endpoint is policy-backed and denies builder self-approval. The UI is not an independent authorization system.

## Reference Architecture Delta Closure

The baseline architecture audit identified local Level 4 gaps. Their current status is:

- readiness route separate from health: implemented through `GET /ready`;
- local configuration model for host, port, database, evidence root, UI root, plugin root, and log level: implemented in `ControlPlaneConfig`;
- local event ingestion API: implemented through `POST /events` and `GET /events`;
- append-only event ledger and replay projection: implemented through SQLite events and `/dashboard/replay`;
- client registry with client ids, capabilities, trust state, connection mode, and heartbeat timestamps: implemented;
- task lease model for active multi-client work: implemented;
- dynamic MCP gateway over the existing MCP tools: implemented for role/high-risk filtering and local event emission;
- plugin manifest registry and installation status: manifest schema, artifact evidence, dry-run, security audit, reproducible artifacts, persisted inventory, and local apply/uninstall/rollback writers are implemented;
- durable work queue and scheduler: work-item persistence, transition events,
  local scheduler decisions, local API/CLI work verbs, run/task state projection,
  stale recovery reassignment, and event-derived plugin dashboard status are implemented;
- shutdown and stale-lock recovery evidence: lifecycle model implemented; richer operator recovery guide remains planned;
- Docker compose profile for the Level 4 control plane: implemented through the `level4` profile.

## Lifecycle Model

Implemented lifecycle primitives live in `vad.control_plane.lifecycle`.

- States: `starting`, `ready`, `draining`, `stopped`, and `failed`.
- Controlled shutdown flows from `ready` to `draining` to `stopped`.
- Failure transitions are terminal and evidence-shaped.
- Every transition records actor, reason, source state, target state, and timestamp.
- Stale lock recovery reads a local control-plane lock, removes it only when the heartbeat is older than the configured threshold, and returns a recovery event.

This lifecycle model is used by the local control-plane serving path and deterministic test-start smoke.

## Config Model

Implemented config primitives live in `vad.control_plane.config`.

- Default config path: `.vad/control-plane/config.json`.
- Default bind host: `127.0.0.1`.
- Default port: `8080`.
- Default database path: `.vad/control-plane/vad.sqlite3`.
- Default evidence root: `.vad/control-plane/evidence`.
- Default UI root: `.vad/control-plane/ui`.
- Default plugin root: `.vad/control-plane/plugins`.
- Default log level: `info`.

Environment overrides use explicit `VAD_CONTROL_PLANE_*` names, including `VAD_CONTROL_PLANE_HOST`, `VAD_CONTROL_PLANE_PORT`, `VAD_CONTROL_PLANE_DB`, `VAD_CONTROL_PLANE_EVIDENCE_ROOT`, `VAD_CONTROL_PLANE_UI_ROOT`, `VAD_CONTROL_PLANE_PLUGIN_ROOT`, `VAD_CONTROL_PLANE_LOG_LEVEL`, and `VAD_CONTROL_PLANE_ALLOW_NON_LOCAL_BIND`.

Non-local binds such as `0.0.0.0` fail validation unless `allow_non_local_bind=True` is set explicitly. When enabled, the config exposes a warning string so future CLI/server code can surface the risk. Config values reject inline secret markers such as `api_key=`, `token=`, `password=`, `secret=`, `private_key`, and provider-style `sk-` markers.

Port `0` is accepted for deterministic tests that need an ephemeral local port. Operator-facing defaults still use port `8080`.

This config model is used by `vad control-plane serve` and `vad local-os demo`. Loading a complete config file from disk remains future local hardening.

## Control-Plane Serve Command

Implemented serving primitives live in `vad.control_plane.server` and the CLI exposes:

```bash
vad control-plane serve
```

The command uses `ControlPlaneConfig` defaults, so its default paths are under `.vad/control-plane` and its default bind host is `127.0.0.1`. It reuses the existing UI/API server helper, adds lifecycle transition evidence around startup/readiness/shutdown, and exposes both `GET /health` and `GET /ready`.

For deterministic tests, `vad control-plane serve --test-start` starts the server, marks lifecycle ready, requests controlled shutdown, closes the server, and returns without entering `serve_forever`.

`vad local-os demo` is the one-command local OS demonstrator. It wraps the same local control-plane server with localhost defaults, seeds `seed_multi_client_simulator_store`, builds the dashboard UI, and serves the simulator-backed dashboard from `.vad/local-os` state. `--test-start` uses the same lifecycle smoke path as `control-plane serve`; no live credentials, cloud APIs, or paid model clients are started by this command.

## Docker Compose Profile

`compose.yaml` includes a Level 4 local profile:

```bash
docker compose --profile level4 up vad-control-plane
```

The service runs `vad local-os demo`, binds `0.0.0.0` inside the container with explicit `--allow-non-local-bind`, maps host port `8081` to container port `8080`, seeds deterministic multi-client simulator data, and sets `VAD_LIVE_SERVICES=disabled`.

Smoke routes for the Level 4 profile:

- `GET http://localhost:8081/health`
- `GET http://localhost:8081/ready`
- `GET http://localhost:8081/`
- `GET http://localhost:8081/dashboard`
- `GET http://localhost:8081/dashboard/replay?run_id=multi-client-simulator`
- `GET http://localhost:8081/plugins/status`

`/plugins/status` reports persisted local plugin inventory when records exist and otherwise falls back to a deterministic plugin status seed for local dashboard and compose smoke checks. Reproducible artifact generation, installer dry-runs, installed-artifact smokes, local install docs, plugin inventory persistence, and guarded local apply/uninstall/rollback writers exist. The seed is not a claim that package installation has been applied to a real user profile.

The compose demo gate starts the profile, checks that the dashboard exposes simulator clients and replay data, then runs `docker compose --profile level4 down` to verify cleanup completes.

## Event Envelope Model

Implemented event primitives live in `vad.control_plane.events`.

The initial envelope includes event id, sequence, timestamp, safe client id, optional client label, run id, task id, kind, status, actor, role, evidence digest, and summary. Event kind and status are strict enums; unknown values fail validation until an explicit extension path is designed. Identifiers reject path and control separators so client/run/task ids cannot be reused as unsafe paths. `client_label` preserves display names such as `Generic MCP/A2A` while `client_id` remains safe for local storage and indexing.

## Event Store

Implemented event persistence lives in `vad.server.db.store` as schema version 3.

- Table: `control_plane_events`.
- Writes are append-only through `append_control_plane_event`.
- Duplicate event ids fail closed through the primary key.
- Reads use deterministic ordering by `sequence`, `created_at`, and `event_id`.
- Indexes support all-events, run-filtered, and client-filtered queries.

## Event Ingestion API

Implemented event ingestion lives on the local UI/API server:

- `POST /events` validates a `ControlPlaneEvent` envelope and appends it to the event store.
- `GET /events` returns the deterministic event ledger for local dashboard/replay consumers.
- The HTTP handler rejects non-local clients before parsing `POST /events` bodies.
- If event storage is not configured, ingestion fails with a policy-shaped denial.
- Invalid envelopes fail closed and are not persisted.
- Duplicate event ids return a conflict and are not overwritten.
- Privileged events (`file_change_applied`, `approval_recorded`, `signer_event`, `deployment_event`, and `recovery_action`) are checked against explicit local roles. Denied privileged submissions persist a `policy_denied` event so the dashboard can show the failed orchestration attempt.

## Dashboard Replay Projection

When the control-plane event ledger has rows, `GET /dashboard` rebuilds the dashboard from events instead of relying on precomputed UI state. The projection returns:

- activity stream and event timeline entries ordered by sequence, timestamp, and event id;
- status, kind, and client counts;
- latest task-board state by task id plus fixed dashboard columns;
- local plugin/client status from heartbeat and message events;
- stale client ids based on the last observed event time;
- active clients from the durable client manifest and heartbeat registry;
- proof and terminal status panels from structured stream records, falling back
  to replayed proof/tool/recovery events;
- run summaries from persisted run evidence.

If the event ledger is empty, the endpoint keeps the existing `dashboard_activity` compatibility path. If a stored event payload is corrupt or no longer validates, the dashboard returns `422 invalid_control_plane_event_ledger` with details instead of rendering a partial or misleading projection. Explicit stale or disconnected client events override recency so replay does not hide a stale scan result just because the scan event is recent.

`GET /dashboard/replay` exposes the event-ledger projection explicitly. Without a query string it returns the same dashboard payload as the current event-backed `/dashboard` snapshot for deterministic fixtures; with `?run_id={run_id}` it filters the replay to that completed run's ledger events and matching persisted run evidence. The response includes `replay` metadata with mode, run id, event count, and source so operators can distinguish replayed ledger state from the legacy `dashboard_activity` compatibility path. Replay mode does not create a second dashboard store and does not read raw terminal logs.

The browser dashboard renders `event_timeline` as a filterable live timeline. Operators can filter by run, client, status, kind, and role. Tool-call events and policy-denial events remain visible in the same stream, so local MCP activity and denied privileged actions can be inspected without relying on terminal logs.

The browser dashboard also renders `task_board_columns` with fixed `active`, `blocked`, `passed`, `failed`, and `needs_human` columns. The API merges replayed task events, durable work items, and durable task leases so each card can show work-item status plus lease owner and expiry when the task has a lease. Lease state is mapped for dashboard scanning: active leases remain active, released leases appear as passed, and expired leases appear as failed. Work-item lifecycle state is preserved as `work_item_status` while mapped into the fixed dashboard columns: planned, queued, assigned, running, verifying, and requeued work appears active; blocked work appears blocked; waiting-for-human work appears needs-human; approved and completed work appears passed; failed and cancelled work appears failed.

Structured proof and terminal streams persist in the local SQLite
`proof_stream_records` and `terminal_stream_records` tables as schema version
9. `ProofStreamRecord` tracks run id, task id, status, client, actor, role,
start/finish times, recovery event id, evidence URL, evidence digest, and a
redacted dashboard summary. `TerminalStreamRecord` tracks run id, task id,
kind, status, client, role, event id, evidence digest, and a redacted dashboard
summary.

When structured stream records exist, `/dashboard` uses them for `proof_status`
and `terminal_status` instead of inferring those panels from generic event
summaries. If the stream tables are empty, the proof panel still derives
`proof_status` from `proof_started` and `proof_finished` events. Failed proof
rows link to `/runs/{run_id}/evidence` when run evidence is addressable, and
include the matching `recovery_action` event id when present. The terminal
fallback derives `terminal_status` from proof, tool-call, policy-denial, and
recovery events. Both paths redact summaries before returning `/dashboard`, so
raw secret markers and provider key patterns are not exposed in dashboard log
text.

## Diff Proposal And Policy-Checked Apply

Implemented diff proposal primitives live in `vad.repo.diff_workflow`.

`create_diff_proposal` records a proposed unified diff before any workspace
mutation. The proposal includes run id, task id, submitting actor, role, patch
digest, changed file list, timestamp, and summary so builder output can be
reviewed as a first-class orchestrator object.

`apply_approved_diff_proposal` requires both verifier and release-guardian
`PolicyDecision` approvals before applying the proposal. Denied decisions
return a replayable apply record and leave the workspace unchanged. Approved
applies reuse the existing `apply_unified_diff` workspace sandbox, capture the
patch journal before mutation, compare the actually changed files to the
approved proposal, and record after-file SHA-256 digests. If applied files do
not match the approved proposal, the journal rollback path is invoked and the
apply record reports the blocker.

Persisted diff proposals and apply records live in SQLite schema version 10.
`POST /diff-proposals`, `GET /diff-proposals`, `GET /diff-proposals/{id}`, and
`POST /diff-proposals/{id}/apply` expose the same workflow through the local
API. Matching CLI verbs live under `vad diff-proposals`.

## Demo Seed Data

The local demo seeders convert dashboard activity fixtures into control-plane events through the ingestion service. This keeps the first-screen dashboard realistic for simultaneous local coding services while making the data replayable from the Level 4 event ledger. The older `dashboard_activity` table remains available for compatibility and older helpers, but seeded Level 4 dashboards prefer event replay whenever event rows exist.

`seed_multi_client_simulator_store` is the reusable simulator fixture used by `vad local-os demo`. It registers Codex, Antigravity, Claude Code, VS Code, Windsurf, Cursor, OpenCode, and Generic MCP/A2A client manifests, records active heartbeats for each client, and appends replayable tool-call, work-item, proof, signing, and fake deployment events for each client through the same registry and event-ingestion services as the API. The fixture writes only local SQLite/evidence state. A long-running simulator process is not required for the current deterministic local OS demo.

`seed_multi_client_role_separation_store` extends the simulator with a deterministic role-separation scenario. It records planner work on Antigravity, builder work and lease ownership on Codex, verifier proof on VS Code, auditor review on Windsurf, and release-guardian approval on OpenCode. The scenario persists a denied self-approval attempt by the builder, a permitted release-guardian approval, a stale Codex heartbeat, an expired builder lease, and a `recovery_action` event queued by Generic MCP/A2A so replay consumers can inspect the recovery path.

## Client Manifest Model

Implemented client manifest primitives live in `vad.control_plane.clients`.

The manifest records safe client id, display name, client type, version, connection mode, supported capabilities, workspace root, and trust state. Current client types cover Codex, Claude Code, VS Code, Antigravity, Windsurf, Cursor, OpenCode, generic MCP clients, and other local clients. Capability assertions are descriptive only: a manifest cannot request or receive policy permissions through `supported_capabilities`.

## Client Registration

Implemented client registration stores manifests in the local SQLite database and writes control-plane evidence events for registration lifecycle changes.

- API routes: `POST /clients/register`, `GET /clients`, and `DELETE /clients/{client_id}`.
- CLI routes: `vad clients register`, `vad clients list`, and `vad clients unregister`.
- Duplicate client ids fail with `duplicate_client`; unregistering a missing client fails with `client_not_found`.
- Registration and unregistration events use the client id as actor so client identity appears in the replay ledger.

## Client Heartbeats And Stale Detection

Implemented heartbeat primitives store runtime status in the same SQLite database as client manifests and append heartbeat evidence to the control-plane event ledger.

- Runtime states are `active`, `stale`, and `disconnected`.
- API routes: `POST /clients/{client_id}/heartbeat`, `POST /clients/stale-scan`, and `GET /clients`.
- CLI routes: `vad clients heartbeat`, `vad clients mark-stale`, and `vad clients list`.
- `GET /clients` returns manifest plus runtime status, last heartbeat, run, task, and lost lease ids.
- `GET /dashboard` includes `active_clients` entries with client type, status, heartbeat age, capabilities, connection mode, trust state, last run/task ids, and lost lease ids.
- Stale scans mark clients stale when the last heartbeat is older than the
  configured threshold, expire that client's active leases, requeue assigned
  or in-progress work items, clear abandoned work ownership, and append
  replayable stale heartbeat, work-item, and recovery-action events.
- API and CLI stale-scan responses include `recovered_work_items` and
  `recovery_events` alongside the existing `stale_clients` list.
- The dashboard marks stale clients from explicit stale events, heartbeat age during projection, and the registry-backed `active_clients` status used by the browser view.

## Task Leases

Implemented task leases persist active multi-client work ownership in the local SQLite database and append `task_lease` events to the replay ledger.

- Runtime states are `active`, `released`, `expired`, and `blocked`.
- API routes: `POST /leases`, `GET /leases`, `POST /leases/{task_id}/renew`, `POST /leases/{task_id}/release`, `POST /leases/{task_id}/expire`, and `POST /leases/{task_id}/approval-check`.
- A lease records task id, run id, client id, role, actor, acquisition time, expiry time, status, and release reason.
- `GET /dashboard` includes `task_leases` and merges lease owner/expiry into `task_board_columns`.
- Only the lease holder can renew or release an active lease.
- Duplicate active acquisition returns a policy-shaped denial instead of stealing ownership.
- Stale-client scans expire active leases held by the stale client and return those task ids as `lost_task_leases`.
- Approval transition checks require `release_guardian` role and deny the actor/client that built the work from approving its own lease transition.

## Work Items

Implemented work-item primitives live in `vad.control_plane.work_items`.

- `WorkItem` records safe work item id, run id, title, description, required
  role, optional requested capability, priority, status, timestamps, assigned
  client id, lease id, evidence digest, blocked reason, and optional
  `WorkItemGovernance`.
- `WorkItemGovernance` records effort type, MEES estimate, token budget,
  approval requirement, live-service opt-in state, high-risk state, operator
  intent reference, and approval reference. MEES under 50 requires approval;
  high-risk work and live-service opt-ins require both approval and a current
  operator intent reference before the work item can be created.
- Runtime states cover `planned`, `queued`, `assigned`, `running`, `blocked`,
  `waiting_for_human`, `verifying`, `approved`, `completed`, `failed`,
  `cancelled`, and `requeued`.
- `vad.server.db.store` persists work items in the local SQLite `work_items`
  table as schema version 7.
- Work-item reads can filter by run, status, and assigned client, ordered by
  priority and creation time for scheduler use.
- `vad.server.api.work_items.WorkItemService` creates work items and transitions
  them through explicit states while appending `work_item` events to the local
  control-plane event ledger.
- `vad.server.api.work_items.WorkItemSchedulerService` selects queued or
  requeued work by priority, filters active trusted clients by requested
  capability, prefers eligible clients with fewer active leases, and assigns
  through the same replayable transition service.
- Local API routes cover `POST /work-items`, `GET /work-items`,
  `GET /work-items/{work_item_id}`, and `POST
  /work-items/{work_item_id}/{assign|block|complete|fail|cancel|requeue}`.
- CLI routes cover `vad work-items create`, `list`, `show`, `assign`, `block`,
  `complete`, `fail`, `cancel`, and `requeue` against the same SQLite-backed
  services. `vad work-items create` accepts governance flags for effort type,
  MEES estimate, token budget, approval requirement, live-service opt-in,
  high-risk state, operator intent reference, and approval reference.
- `GET /dashboard` projects durable work items into `task_board_columns` and
  preserves `work_item_id`, `work_item_status`, priority, requested capability,
  governance, evidence digest, blocked reason, and lease linkage on board
  cards.
- Invalid transitions fail closed, keep the stored work item unchanged, and
  append `policy_denied` evidence for replay.
- Requeue transitions clear previous work-item assignment and lease linkage so
  stale-client recovery can safely return abandoned work to the scheduler.
- Persistence, transition events, and dashboard projection do not create
  dashboard-only fake state. Stale-client recovery requeues abandoned work;
  automatic reassignment to a different client remains a scheduler action.

## Local SDK And CLI Event Emitter

Implemented local client helpers live in `vad.control_plane.sdk`.

- `LocalControlPlaneClient.from_db_path(path)` creates a deterministic local SDK client over the same SQLite-backed services used by the API.
- SDK methods cover `register`, `heartbeat`, `emit_event`,
  `poll_assigned_work`, `receive_next_work`, `accept_work`, `reject_work`,
  `block_work`, `complete_work`, and `fail_work`.
- `emit_event` uses `ControlPlaneEventService`, so privileged events receive the same allow/deny policy behavior as `POST /events`.
- Work-intake helpers use the same scheduler and `WorkItemService` transition
  paths as API and CLI operations. Local connectors can poll work assigned to
  their client id, ask the scheduler to assign the next queued item, accept
  assigned work by moving it to running, reject by requeueing and clearing
  ownership, block with a reason, complete with optional evidence digest, or
  fail work. These helper calls append replayable work-status events through
  the local SQLite event ledger and require no HTTP server, live credentials,
  cloud service, or paid model call.
- Per-client fixture smoke coverage proves Codex, Claude Code, VS Code,
  Cursor, Windsurf, OpenCode, Generic MCP/A2A, and the Antigravity generic
  fallback can each register, heartbeat, receive assigned work, emit a local
  proof event, and complete one assigned local task through the SDK contract.
- CLI fallback: `vad events emit` validates a full `ControlPlaneEvent` envelope and writes to the local event ledger without HTTP or network access.
- Policy-denied CLI emissions exit nonzero and persist a `policy_denied` event for replay.

## MCP Gateway Tool Registry

Implemented gateway registry primitives live in `vad.control_plane.mcp_gateway`.

- The registry records every currently exposed MCP tool name, description, input schema, risk level, required role, and tool-call event emission policy.
- `vad.adapters.mcp.TOOLS` is generated from the registry so stdio `tools/list` cannot drift from the governed metadata.
- The registry marks current high-risk tools explicitly: `repo_patch`, `repo_run`, and `sign_verify`.
- Default event policy records started, finished, and failed tool-call events with control-plane tool-call event kinds.
- Dynamic filtering is role-aware: unknown clients default to the `observer` role and receive only safe read-only tools.
- `tools/list` may include `client_id`, `run_id`, `role`, and `approved_high_risk_tools`; the server returns both visible tools and `tool_visibility_audit` records for visible and hidden tools.
- High-risk tools remain hidden until the client has the matching role and explicitly lists the tool in `approved_high_risk_tools`.
- `tools/call` reuses the same visibility policy before handler execution, so hidden tools cannot be invoked by name. High-risk tools such as `repo_patch`, `repo_run`, and `sign_verify` are denied unless explicitly approved for that call.
- MCP tool calls may include `control_plane_db`, `client_id`, `actor_id`, `role`, `run_id`, and `task_id` arguments to emit local control-plane events through the SQLite-backed SDK without HTTP or network access.
- Tool calls emit `tool_call_started` events before handler execution and `tool_call_finished` events after success or failure. Denied/error paths also emit a blocked `tool_call_finished` event with a denial summary so policy denials are replayable.
- Started-event summaries redact sensitive argument keys such as secrets, tokens, passwords, API keys, private keys, and key files before persistence.
- Finished events link `evidence_digest` when the MCP result carries or returns an evidence hash or payload digest.
- Stdio smoke tests cover generic MCP discovery plus Claude Code, Codex, VS Code, and OpenCode fixture client roles so future gateway changes cannot silently broaden or narrow tool exposure without audit evidence.

## Local HTTP MCP Transport Decision

Stdio remains the baseline MCP transport for compatibility and plugin packaging. `vad mcp run` is still the default integration path for generic MCP clients and current manual configuration snippets.

The generic MCP package is generated by `vad.control_plane.generic_mcp_package`. It provides a validated `vad-generic-mcp` manifest, common stdio `mcpServers` and direct server-map config snippets, and the manual fallback command `vad mcp run`. Package smoke coverage proves generic clients can discover observer-safe tools and emit local control-plane tool-call events through the stdio path.

The Claude Code package is generated by `vad.control_plane.claude_code_package`. It provides a validated `vad-claude-code-local` manifest, a workspace `.mcp.json` local MCP config, VAD builder/verifier/auditor role prompts, dry-run-safe config paths, and the same manual fallback command `vad mcp run`.

The Codex package is generated by `vad.control_plane.codex_package`. It provides a validated `vad-codex-local` manifest, a reviewable `.codex-plugin/plugin.json`, a local `./.mcp.json` stdio server config, VAD builder/verifier/auditor skill guides under `./skills/`, dry-run-safe config paths, and the same manual fallback command `vad mcp run`. The package follows the current Codex plugin manifest shape verified from installed plugin manifests and the plugin-creator spec: plugin metadata lives in `.codex-plugin/plugin.json`, skills are referenced by `./skills/`, and the local MCP server is referenced by `./.mcp.json`.

The VS Code package is generated by `vad.control_plane.vscode_package`. It provides a validated `vad-vscode-local` manifest, a workspace `.vscode/mcp.json` config using VS Code's current `servers` MCP shape, a workspace `.vscode/tasks.json` dashboard task for `vad ui serve --host 127.0.0.1 --port 8080 --seed-level3-demo`, user-profile dry-run review output, trust-safe documentation, and the same manual fallback command `vad mcp run`.

The Cursor package is generated by `vad.control_plane.cursor_package`. It provides a validated `vad-cursor-local` manifest, a project `.cursor/mcp.json` config using Cursor's current `mcpServers` stdio shape, project rules under `.cursor/rules/*.mdc`, global `.cursor/mcp.json` dry-run review output, and the same manual fallback command `vad mcp run`.

The Windsurf package is generated by `vad.control_plane.windsurf_package`. It provides a validated `vad-windsurf-local` manifest, a user `.codeium/windsurf/mcp_config.json` dry-run review path using Windsurf Cascade's current `mcpServers` stdio shape, preferred workspace rules under `.devin/rules/*.md`, a manual verification workflow under `.windsurf/workflows/vad-verify.md`, and the same manual fallback command `vad mcp run`.

The OpenCode package is generated by `vad.control_plane.opencode_package`. It provides a validated `vad-opencode-local` manifest, a project `opencode.jsonc` using OpenCode's current `mcp` local-server shape, Markdown agents under `.opencode/agents/*.md`, per-agent permission gates for VAD MCP tools, a global `.config/opencode/opencode.json` dry-run review path, and the same manual fallback command `vad mcp run`.

Antigravity discovery is recorded by `vad.control_plane.antigravity_discovery`. Current public Antigravity documentation does not expose a stable local MCP, plugin, or config file surface, so VAD does not generate a first-class Antigravity package. Antigravity should use the `vad-generic-mcp` stdio fallback until a documented local integration contract exists.

The local control-plane HTTP server also exposes an optional `POST /mcp` endpoint for local JSON-RPC MCP requests. It reuses the same request handling, tool filtering, runtime authorization, and event-emission path as stdio. The HTTP route is local-only at the server handler boundary; non-local requests receive `local_only_route`, matching the event ingestion route posture.

This endpoint adds no dependency and does not implement remote/cloud MCP hosting. It is a local/offline bridge for clients or tests that can post JSON-RPC to the already-running control-plane server. A richer streamable HTTP/SSE transport remains future work unless a later client package needs it and can keep the same local-only security boundary.

## Plugin Manifest Schema

Implemented plugin manifest primitives live in `vad.control_plane.plugins`, with a checked-in JSON schema at `schemas/vad-plugin-manifest.schema.json`.

The manifest records:

- `plugin_id`, target client, and semantic version;
- local command executable, arguments, and non-secret environment values;
- reviewable config paths scoped to user, workspace, or project config;
- requested permissions with human-readable reasons;
- tool grants with role and high-risk metadata;
- prompt ids, roles, and relative prompt paths;
- relative artifact paths mapped to 64-character SHA-256-style checksums.

The manifest schema is validation only. It does not install plugins, trust plugin artifacts, approve high-risk tools, or write client configuration. High-risk tools are explicitly forbidden from being approved by default in the manifest contract.

## Plugin Artifact Digest And Signature Model

Implemented plugin artifact primitives also live in `vad.control_plane.plugins`.

- `compute_plugin_artifact_hash` derives a deterministic artifact digest from the validated manifest digest plus sorted artifact file digests.
- `PluginArtifactSignature` wraps the existing local development `SignatureEnvelope`; the signing secret is never serialized by the artifact model.
- `verify_plugin_artifact` returns replayable verification evidence including plugin id, version, artifact digest, manifest digest, file count, whether a signature was present, whether it verified, and the signer key id when available.

Unsigned artifact verification proves only the digest evidence. Signed verification is optional and uses the existing local HMAC development signer. This model still does not install, publish, trust, or roll back plugin artifacts.

## Plugin Installer Dry-Run Framework

Implemented installer preview primitives live in `vad.control_plane.plugins`.

`create_plugin_installer_dry_run` accepts a validated plugin manifest and explicit workspace/user config roots, verifies the artifact digest evidence, and returns a typed plan containing the exact config files, scopes, planned changes, config content, and rollback metadata. The dry-run contract is review-only: `dry_run` is always true and `writes_performed` is zero.

`vad plugins install MANIFEST --dry-run` prints the same plan as JSON, or writes the preview JSON with `--out`. It does not create parent directories or client config files. Planned writes are limited to manifest paths scoped to user or workspace config roots; project-scoped install paths and relative path escapes are rejected before any write path can be produced.

Rollback metadata records `restore_or_remove` operations and deterministic `.vad-backup` paths for each planned config file. Installed-artifact smoke tests exercise this metadata against temp workspace/home roots. Local apply, uninstall, and rollback writers consume the same reviewed dry-run contract and re-check every write and backup path against the approved workspace/user roots before touching files.

## Plugin Inventory, Status API, And Dashboard

Implemented plugin inventory primitives live in `vad.control_plane.plugins` and
`vad.server.db.store`.

`PluginInventoryRecord` persists local review state, applied config hashes,
backup paths, uninstall status, rollback status, dashboard status, publication
readiness, artifact digest, and manifest digest in the SQLite
`plugin_inventory` table as schema version 8.

`apply_plugin_installer_plan`, `uninstall_plugin_installation`, and
`rollback_plugin_installation` are local writer primitives over an explicit
operator approval reference. Apply writes canonical JSON config payloads from a
reviewed dry-run, captures backups for existing files, records SHA-256 config
hashes, and returns inventory-ready evidence. Uninstall and rollback verify the
recorded hashes before mutation so local drift blocks destructive cleanup.

The local control plane exposes plugin dashboard state through `GET
/plugins/status` and includes the same records in `/dashboard`. When persisted
inventory records exist, the endpoint reports `status: inventory` and projects
those records into `PluginStatusRecord` values for the dashboard. When the
inventory is empty, the endpoint keeps the deterministic plugin status seed for
local dashboard and compose smoke checks.

The seed uses typed `PluginStatusRecord` values from `vad.control_plane.plugins` and covers the four dashboard states:

- `installed` for a workspace-installed Codex local plugin;
- `available` for a Claude Code package ready for dry-run review;
- `needs_review` for a VS Code workspace config that requires operator review;
- `failed` for a Cursor install validation failure.

Each status record includes the target client, semantic package version, local version, publication readiness, summary, and action required when operator work remains. Publication readiness is local dashboard metadata only: it distinguishes `local_ready`, `dry_run_ready`, `needs_operator_review`, and `blocked`, but it does not publish artifacts or imply marketplace acceptance.

The browser dashboard renders these records in the Plugins panel. This is still
local inventory/status plumbing, not a package manager. Event-derived plugin
status updates beyond explicit inventory writes remain planned local hardening.

## Plugin Artifact Security Audit

Implemented plugin audit primitives live in `vad.control_plane.plugins`.

`audit_plugin_artifact_security` reviews a validated manifest together with a generated installer dry-run plan before any local writer is called. The audit returns a typed pass/fail result and findings for:

- secret markers in generated config or manifest fields;
- install operations that escape the approved workspace/user config roots;
- high-risk tools that attempt default approval;
- non-local HTTP endpoints that would create a cloud default.

The audit is intentionally paired with `create_plugin_installer_dry_run`: dry-run plans must remain no-write (`dry_run=True`, `writes_performed=0`) and operations must stay under explicit user/workspace roots. This does not make plugin artifacts trusted automatically; local writers still require explicit approval evidence and re-check guarded paths before mutation.

## No-Cloud Boundary

The current local distribution is local only:

- no hosted VAD SaaS;
- no cloud dashboard;
- no cloud-hosted MCP gateway;
- no remote team event aggregation;
- no live production deployment provider in default flows;
- no paid model calls in default tests;
- no committed or generated secret-bearing config.

All Level 4 control-plane behavior must run with local filesystem/SQLite state, bind to localhost by default, and pass disposable Docker verification.
