# Level 4 Local Operator Guide

This guide runs the local Level 4 control plane as an operator-owned, offline
system. It uses localhost HTTP, stdio MCP, SQLite, and local files. It is not a
hosted VAD SaaS, managed tenant, cloud dashboard, remote MCP gateway, production
deployment, live provider runner, or package publication path. This is not a hosted VAD SaaS.

Keep `VAD_LIVE_SERVICES=disabled` for the current local distribution. Do not add live credentials,
API keys, tokens, passwords, signing secrets, private keys, or provider
credentials to control-plane, dashboard, compose, or MCP configuration.

## Start And Stop Local Control Plane

For the one-command simulator-backed local OS demo:

```bash
VAD_LIVE_SERVICES=disabled vad local-os demo
```

The command binds to `127.0.0.1:8080` by default, builds the dashboard UI, seeds
multi-client simulator state under `.vad/local-os`, and serves the local
control-plane API. Use `vad local-os demo --test-start` for a deterministic
start, ready, and stop smoke check.

For the lower-level local control-plane server:

```bash
VAD_LIVE_SERVICES=disabled vad control-plane serve
```

The default state lives under `.vad/control-plane`, the default host is
`127.0.0.1`, and readiness is exposed through:

- `GET http://127.0.0.1:8080/health`
- `GET http://127.0.0.1:8080/ready`

For an isolated compose run:

```bash
docker compose --profile level4 up -d --build vad-control-plane
curl -fsS http://127.0.0.1:8081/health
curl -fsS http://127.0.0.1:8081/ready
docker compose --profile level4 down
```

The compose service binds `0.0.0.0` only inside the container with explicit
`--allow-non-local-bind`, maps host port `8081`, and still runs with
`VAD_LIVE_SERVICES=disabled`. Treat it as a local smoke environment, not as a remote service.

## Connect Clients

Use stdio MCP as the baseline manual connection path:

```bash
vad mcp run
```

Register each local client with a safe id and a human-readable display name:

```bash
vad clients register \
  --client-id codex-local \
  --display-name Codex \
  --client-type codex \
  --workspace-root . \
  --capability repo_assess \
  --capability evidence_inspect
```

Record runtime liveness and current work with heartbeats:

```bash
vad clients heartbeat \
  --client-id codex-local \
  --run-id multi-client-simulator \
  --task-id local-build
```

Use `vad clients list` to inspect manifest and runtime status. Capability names
are descriptive evidence only; they do not grant policy permissions. High-risk
MCP tools still require role and per-call approval through the governed MCP
gateway.

For reviewed local package snippets and per-client config paths, use the local
install guide and generated artifacts before copying config into Codex, Claude
Code, VS Code, Cursor, Windsurf, OpenCode, Antigravity, or generic MCP clients.

## Read Dashboard

Open the browser dashboard or query the local API:

```bash
curl -fsS http://127.0.0.1:8080/dashboard
curl -fsS 'http://127.0.0.1:8080/dashboard/replay?run_id=multi-client-simulator'
```

For compose, replace port `8080` with `8081`.

The operator-facing dashboard payload includes:

- `active_clients` for registered clients, runtime status, heartbeat age,
  connection mode, trust state, capabilities, current run/task ids, and lost
  lease ids;
- `event_timeline` for replayable tool calls, proof events, policy denials,
  recovery actions, and other local orchestration evidence;
- `task_board_columns` for fixed `active`, `blocked`, `passed`, `failed`, and
  `needs_human` board states;
- `task_leases` for owner, role, run id, expiry, status, and release reason;
- `proof_status` and `terminal_status` for proof/recovery evidence without raw
  terminal logs or secret-bearing text;
- `plugin_status` for local package readiness and operator review state.

Use `/dashboard` for the current snapshot and `/dashboard/replay` when you need
to reconstruct state from the append-only local event ledger. Replay mode does
not read raw terminal logs and does not create a separate dashboard authority.

## Recover Stale Clients And Blocked Tasks

Run stale detection when a client stops heartbeating:

```bash
vad clients mark-stale --stale-after-seconds 120
```

The equivalent local API path is:

```bash
curl -fsS -X POST http://127.0.0.1:8080/clients/stale-scan \
  -H 'content-type: application/json' \
  -d '{"stale_after_seconds":120}'
```

Stale scans mark the client `stale`, append replayable heartbeat evidence, and
expire active task leases held by that client. The response includes
`lost_task_leases`; the dashboard reflects those ids in `active_clients`,
`task_leases`, and `task_board_columns`.

For a specific blocked or abandoned task, inspect leases first:

```bash
curl -fsS http://127.0.0.1:8080/leases
```

Then expire an abandoned lease explicitly:

```bash
curl -fsS -X POST http://127.0.0.1:8080/leases/local-build/expire \
  -H 'content-type: application/json' \
  -d '{}'
```

Before moving a blocked build toward release, run the approval transition check:

```bash
curl -fsS -X POST http://127.0.0.1:8080/leases/local-build/approval-check \
  -H 'content-type: application/json' \
  -d '{"actor":"opencode-local","actor_role":"release_guardian","client_id":"opencode-local"}'
```

The release guardian role is required for approval transitions. Builder self-approval is denied, including attempts by the client that owned the build lease. Denials are persisted as policy evidence and remain visible in
`event_timeline`.

When recovery is complete, verify the dashboard shows the stale client, expired
lease, and recovery action before assigning replacement work to another client.
Do not delete the local SQLite database to hide blocked work; use replayable
stale scans, lease expiry, and approval checks so future operators can audit the
handoff.

## Command Coverage

- Tested: `vad local-os demo --test-start`, `vad control-plane serve --test-start`,
  `vad clients register`, `vad clients heartbeat`, `vad clients mark-stale`,
  `vad mcp run`, and `docker compose --profile level4 up -d --build vad-control-plane`.
- Tested routes: `/health`, `/ready`, `/dashboard`,
  `/dashboard/replay?run_id=multi-client-simulator`, `/clients/stale-scan`,
  `/leases`, `/leases/{task_id}/expire`, and
  `/leases/{task_id}/approval-check`.
- Tested recovery evidence: stale clients, lost task leases, expired leases,
  denied builder self-approval, permitted `release_guardian` approval checks,
  `event_timeline`, `active_clients`, `task_board_columns`, `task_leases`,
  `proof_status`, `terminal_status`, and `plugin_status`.
- Illustrative: command examples assume the default local ports are available
  and use example client/task ids.
- Not implemented: hosted VAD SaaS, managed tenancy, cloud dashboard, remote MCP
  gateway, live production providers, automatic client install, marketplace
  publication, automatic artifact trust, and production key management.
- Not implemented: marketplace publication.
