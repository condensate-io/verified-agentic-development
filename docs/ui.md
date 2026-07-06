# Hosted API And UI

VAD currently exposes a small hosted API foundation for local inspection. It is a policy-bound control surface and does not grant write, approval, deployment, or signing authority.

## API Foundation

The server module is available through `vad.server.app`.

Implemented local routes:

- `GET /health` returns server health.
- `GET /runs` lists valid run evidence JSON files from a configured evidence directory.
- `GET /runs/{run_id}` and `GET /runs/{run_id}/evidence` return validated run evidence plus its deterministic evidence digest.
- `GET /runs/{run_id}/approvals` returns persisted approval decisions for a run when SQLite persistence is configured.
- `POST /actions/approve` records an approval decision when SQLite persistence is configured.

Run evidence is loaded from `{evidence_root}/{run_id}.json`. Invalid evidence files are skipped by the list endpoint and rejected by the detail endpoint.

When a SQLite database path is supplied, the API reads run evidence from the persisted server store instead. The store is versioned with `PRAGMA user_version`, verifies run evidence digests on reload, and persists approval events with actor, action, timestamp, policy decision, and linked evidence digest.

Approval requests require `run_id`, `actor`, `actor_role`, and `action`. The current implemented approval action is `approve_release`. Only the `release_guardian` actor role can approve release actions, and the original builder for the run cannot approve their own work. Authorized and denied approval attempts are both persisted for audit.

The implementation uses the Python standard library HTTP server so the default test suite remains dependency-free and offline.

## UI Application

The static UI assets live in `vad/ui/static` and can be built with:

```bash
python -m vad.ui.build --out /tmp/vad-ui
```

The build step copies `index.html`, `styles.css`, and `app.js`, then validates that the page references the expected assets and contains the run list, evidence detail, and approval panel mount points.

Implemented UI views:

- run list from `GET /runs`;
- evidence detail from `GET /runs/{run_id}/evidence`;
- release approval form through `POST /actions/approve`;
- denied approval result rendering for policy failures.
- status summary, activity stream, work-item list, swarm view, provider view, signing view, and deployment view from `GET /dashboard`.
- coding-system/client attribution for dashboard activity, including actor, role, task id, evidence digest, and policy decision when recorded.
- active clients view from `GET /dashboard`, including client type, runtime status, heartbeat age, supported capabilities, connection mode, and stale-client highlighting.
- task board from `GET /dashboard`, with active, blocked, passed, failed, and needs-human columns plus lease owner and expiry when a lease exists.
- proof and terminal status panels from `GET /dashboard`, with proof started/finished state, redacted terminal summaries, and recovery links for failed proofs.
- plugin status view from `GET /dashboard`, with installed, available, needs-review, and failed states plus local version and publication readiness.
- live event timeline from `GET /dashboard`, including filters for run, client, status, kind, and role.
- replay mode from `GET /dashboard/replay`, including whole-ledger replay and selected-run replay when a run is selected.

The server can serve the built UI directory when created with a `ui_root`. Static file serving is contained to that directory and API routes remain handled by the JSON API.

Dashboard activity is persisted in SQLite through the server store. The current activity model is intentionally generic so SDK, MCP, A2A, swarm, provider, signing, and deployment wiring can all report status through one audit-friendly path without giving the UI direct authority over those systems.

The active clients panel is backed by the durable client manifest and heartbeat tables, not by static fixture labels. `active_clients` entries include safe client id, display name, client type, connection mode, trust state, supported capabilities, last heartbeat, heartbeat age in seconds, last run/task ids, runtime status, and lost lease ids. Stale clients render with a distinct warning state.

The event timeline is backed by `event_timeline` entries from the replay projection. Browser filters operate locally over run id, client display label, status, event kind, and role. Tool-call events (`tool_call_started` and `tool_call_finished`) and `policy_denied` events render as timeline rows with distinct event-kind/status attributes so operators can see orchestration activity and denied actions without reading raw logs.

The dashboard Replay control calls `GET /dashboard/replay`. If an operator selected a run, the browser includes `run_id` and reconstructs that completed run from the append-only event ledger; otherwise it replays the whole ledger. Replay payloads use the same dashboard panels as the current snapshot and include replay metadata, so deterministic fixture output can be compared directly with `/dashboard` without maintaining a second UI state table.

The task board is backed by `task_board_columns`, which merges replayed task events with durable `task_leases`. Columns are fixed to `active`, `blocked`, `passed`, `failed`, and `needs_human`; released leases appear as passed work and expired leases appear as failed work. Cards show the task id, summary, kind/status, lease owner, and lease expiry so operators can see who currently owns work and when the lease expires.

The proof and terminal panels are backed by `proof_status` and `terminal_status`. Proof rows are derived from `proof_started` and `proof_finished` events. Failed proof rows link to the run evidence endpoint as recovery evidence when a run id is available, and record the matching `recovery_action` event id when present. Terminal rows are derived from proof, tool-call, policy-denial, and recovery events; summaries are redacted before they are returned to the browser so raw secret markers, provider keys, tokens, passwords, and private-key markers do not appear in dashboard log text.

The plugin status view is backed by typed `plugin_status` records. It shows the target client, local package version, status, publication readiness, summary, and any action required. Current records are deterministic local seed data for dashboard/operator review; they are not an installer registry or evidence of marketplace publication.

## Local CLI Serving

For local development without Docker, run:

```bash
vad ui serve --seed-level3-demo
```

By default, the command binds to `127.0.0.1:8080`, builds the static UI into `.vad/ui/build`, uses `.vad/ui/vad.sqlite3`, and reads file evidence from `.vad/ui/evidence`. Pass `--host`, `--port`, `--db`, `--ui-root`, and `--evidence-root` to override those paths. Use `--seed-demo` for the smaller generic demo or `--seed-level3-demo` for the full Level 3 fixture with success and failure runs. The command uses the same UI build and API server helper as the Docker service.

For the local Level 4 OS demonstrator, run:

```bash
vad local-os demo
```

The command binds to `127.0.0.1:8080`, builds the dashboard UI, starts the local control-plane server, seeds the multi-client simulator fixture, and serves `/dashboard` plus `/dashboard/replay` from local SQLite state under `.vad/local-os`. It does not request live credentials, call cloud services, or start paid model clients. Use `--port`, `--db`, `--evidence-root`, `--ui-root`, and `--plugin-root` to move the local state, or `--test-start` for deterministic smoke tests that start, reach ready, and stop.

## Local Docker Serving

For local end-to-end UI/API testing, run:

```bash
docker compose up vad-ui
```

The `vad-ui` service builds the Python package, builds the static UI assets, starts `python -m vad.server.serve`, seeds deterministic Level 3 fixture data, and serves the API plus UI on port `8080`.

Useful smoke routes:

- `GET http://localhost:8080/health`
- `GET http://localhost:8080/`
- `GET http://localhost:8080/runs`
- `GET http://localhost:8080/dashboard`
- `GET http://localhost:8080/dashboard/replay`

The seeded data is fake and local-only. It includes success and failed-rollout runs, dashboard activity, approval evidence, fake provider routing, signing/deployment activity, rollback feedback, and client attribution for Claude Code, VSCode, Codex, Antigravity, Cursor, Windsurf, OpenCode, and Generic MCP/A2A. It does not require live credentials, cloud services, live provider calls, or paid model calls.

The multi-client simulator fixture is available as `seed_multi_client_simulator_store` and is wired into `vad local-os demo`. It produces client registration and heartbeat state for Codex, Antigravity, Claude Code, VS Code, Windsurf, Cursor, OpenCode, and Generic MCP/A2A, plus replayable tool-call, task, proof, signing, and fake deployment events for each client. The fixture stays offline and uses the same SQLite-backed registry and event ledger as the dashboard.

The role-separation scenario is available as `seed_multi_client_role_separation_store`. It proves the planner, builder, verifier, auditor, and release guardian are split across Antigravity, Codex, VS Code, Windsurf, and OpenCode; records a denied builder self-approval beside a permitted release-guardian approval; and marks the builder stale so the dashboard/replay ledger shows an expired lease plus a recovery action.

## Command Coverage

- Tested: `python -m vad.ui.build`, `vad ui serve --seed-level3-demo`, `vad local-os demo --test-start`, `python -m vad.server.serve --seed-level3-demo`, and `docker compose up vad-ui`.
- Tested routes: `/health`, `/ready`, `/`, `/runs`, `/runs/{run_id}/evidence`, `/dashboard`, `/dashboard/replay`, and denied self-approval through `/actions/approve`.
- Dashboard end-to-end smoke verifies active clients, event timeline, task board columns, replay output, and denied self-approval on the same local simulator-backed server.
- Illustrative: localhost URLs assume the default port is available.
