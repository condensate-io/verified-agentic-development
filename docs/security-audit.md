# Security Audit

This audit records the current hardening checks and open security work for the local Level 3/4 reference implementation. It is scoped to default, offline behavior: fake providers, local fixtures, local SQLite state, stdlib HTTP serving, and deterministic Docker tests. It does not certify the system as secure for remote or multi-user operation.

## How To Use This Checklist

For each item, complete the steps in order: write a bounded negative test in a disposable directory or container; capture the request, response, file/SQLite state, and logs needed to prove the behavior; implement the fix; rerun the same test expecting denial; run the focused and full gates below. Never aim a proof attempt at a real workspace, user profile, credential, or live service. Record the test name, commit, date, and outcome next to the item. A checked box means the fix and regression test passed, not merely that the issue was observed.

Status terms: **code-confirmed** means the present code exposes the behavior; **probe-reproduced** means the bounded local probe observed it; **needs proof** means no bounded exploit test has been run for that item. A reproduced subcase does not clear the remaining tests or fix. These findings are not a completed penetration test.

## Controls Checked

- Filesystem writes are guarded by workspace containment before mutation. Patch application validates target paths through `WorkspaceSandbox` and fails before writing when a diff attempts to escape the repository root.
- Evidence fixtures do not contain committed secret values. The Level 3 demo fixture is scanned for inline API keys, password assignments, private-key markers, token assignments, and provider key patterns.
- Default tests avoid live network calls. Tests may use loopback HTTP servers for local UI/API smoke coverage; non-local HTTP hosts and network primitives outside the local allowlist fail the audit test.
- UI actions use the backend approval service. The current test proves that a request naming the builder is denied; it does not prove that the claimed release-guardian identity is authentic.
- Production deployment apply requires an approval reference. Dry runs and staging applies can remain local, but production apply fails closed without approval, telemetry, and rollback capability.

## Open Attack-Surface Checklist

Work through these in priority order. The paths named below are the current entry points; repeat the review if routes or tool adapters change.

### P0: Authorization And File Mutation

- [ ] **SA-01: Forged diff approval and arbitrary workspace selection (probe-reproduced; fix pending).** `VADApi._diff_proposal_apply_response` in `vad/server/app.py` accepts `workspace_root`, `verifier_decision`, and `release_guardian_decision` from the POST body. `DiffProposalService.apply` and `apply_approved_diff_proposal` trust their `allow` fields. `tests/test_local_hardening_0307.py::test_diff_proposal_api_persists_and_replays_apply_records` currently passes both approvals in the request and verifies that a file changes. **Proof:** in two temporary workspaces, create a proposal and submit `allow: true` for both decisions with no stored approvals; point `workspace_root` at the second workspace. Confirm whether only the selected temporary file changes and preserve the apply record. **Resolve:** take the workspace from server configuration, bind it to the proposal, authenticate verifier and guardian identities, and load their scoped, unexpired decisions from trusted storage. Bind each decision to the proposal ID and patch digest; reject replay or mismatched scope before any write. **Regression:** forged, missing, expired, reused, wrong-workspace, wrong-digest, and self-approved requests leave both workspaces unchanged; two valid independent approvals permit exactly the approved patch.
- [ ] **SA-02: Forged release approval (probe-reproduced; fix pending).** `ApprovalRequest` and `ApprovalService` in `vad/server/api/approvals.py` check the supplied `actor_role` and builder-name inequality, but `POST /actions/approve` has no authenticated actor. **Proof:** use a disposable seeded run and submit a different actor name with `actor_role: release_guardian`; verify whether an allowed approval event is persisted. **Resolve:** derive actor and role from an authenticated principal, enforce builder/verifier/guardian separation against persisted run identity, and bind approval to the exact evidence digest and action. **Regression:** body-supplied role or actor cannot elevate privileges, and a builder cannot approve through an alias.
- [ ] **SA-03: MCP role and high-risk grant spoofing (tool-visibility probe reproduced; handler call pending).** `vad/adapters/mcp.py::_visibility_request_from_args` reads `role` and `approved_high_risk_tools` from tool arguments. **Proof:** call `tools/list` and a harmless high-risk authorization check with a client that claims `builder` and names `repo_patch` in `approved_high_risk_tools`; do not invoke a mutating handler. Record whether visibility/authorization changes. **Resolve:** derive identity and role from the authenticated MCP connection and obtain high-risk grants from a trusted, scoped, expiring server-side record. **Regression:** forged tool arguments never change visibility or authority; a valid grant applies only to its client, run, tool, and lifetime.
- [ ] **SA-04: Other caller-supplied governance records (operator-intent probe reproduced; other routes pending).** Review `POST /operator-intents`, `/clients/register`, `/events`, `/leases`, and `/work-items` for self-declared actor, role, trust state, or privileged event claims. **Proof:** submit a claimed operator intent, trusted client, and privileged event from an unauthenticated local test client; inspect the SQLite rows and derived dashboard state. **Resolve:** use one authenticated authorization layer for every mutating route and derive audit attribution from it. **Regression:** untrusted clients cannot create grants, register as trusted, alter leases/work, or forge governance evidence.

### P1: Network And Execution Boundaries

- [ ] **SA-05: HTTP exposure, cross-origin requests, and read access (local HTTP probes reproduced; non-local access pending).** `vad/server/app.py::make_handler` checks peer IP only for selected POST prefixes; `compose.yaml` publishes `8080:8080` and `8081:8080` on host interfaces. Other writes, DELETE, and reads have no authentication check. **Proof:** in an isolated container/network, enumerate every GET/POST/DELETE route from a second client, and send a mutation with an untrusted `Origin` and `Host` to loopback. Record both allowed and denied results. **Resolve:** bind demo ports to `127.0.0.1` by default; require authenticated principals and per-route authorization before non-local use; validate Host/Origin for browser-facing mutations and prevent DNS rebinding. **Regression:** unauthenticated reads and writes of sensitive state fail; cross-origin browser requests cannot mutate local state; intended local demo flows still work.
- [ ] **SA-06: Untrusted proof execution (sentinel-read probe reproduced; resource-limit tests pending).** `vad/guards/execution.py::ExecutionGuard` allows `python`, `python3`, and `pytest` with arguments, and `subprocess.run` has no timeout, output cap, resource limit, or OS isolation. A proof plan or repository test can execute arbitrary code under the server user's permissions. **Proof:** only inside a disposable, unprivileged container, run a tiny proof fixture that reads an accessible sentinel file and a bounded long-running fixture; record access and timeout behavior. **Resolve:** execute proofs in an ephemeral non-root sandbox with a restricted mount, no secrets, network disabled by default, CPU/memory/process/output limits, and a hard deadline. Treat repository code and proof plans as untrusted regardless of command allowlists. **Regression:** sentinel outside the approved mount is inaccessible; timeout kills the process tree; output and resource limits hold.
- [ ] **SA-07: Request and storage exhaustion (128 KiB persistence reproduced; exhaustion untested).** `Handler.do_POST` reads the declared `Content-Length` without a limit, and the threaded HTTP server has no visible concurrency bound. Diff text, event summaries, and tool output also need size limits. **Proof:** against an isolated server, send a modest over-limit body and several slow requests with a fixed test budget; measure response and memory without exhausting the host. **Resolve:** cap headers, body sizes, patch size, output, stored records, concurrency, and request duration; return controlled 4xx/429 errors. **Regression:** over-limit input is rejected before allocation or persistence and the server remains responsive.
- [ ] **SA-08: File path races and patch integrity (partial-write probe reproduced; path-race test pending).** `vad/repo/patch_apply.py` resolves target paths during validation and later writes by path. **Proof:** in a temporary workspace, test symlink targets and a controlled target swap between validation and write; verify that no sentinel outside the root changes. Also test malformed/multi-file diffs and a failed later write for partial application. **Resolve:** enforce workspace containment at the final open/write, reject unsafe link targets, bind the approved patch digest to the applied bytes, and make multi-file writes atomic or reliably reversible. **Regression:** escapes, races, and partial writes leave all protected files unchanged.

### P2: Agent Data And Supply Chain

- [ ] **SA-09: Prompt injection and tool-result laundering (needs proof).** Repository files, proof output, plugin metadata, peer-agent messages, and dashboard/event text can contain attacker-controlled instructions. **Proof:** place a benign instruction to request an unapproved tool in each source, run the deterministic agent fixture, and record whether the agent attempts the call. **Resolve:** label untrusted content as data, keep authorization outside model output, minimize tool permissions, and require human review for high-impact actions. **Regression:** injected text cannot change grants or bypass a denied action; attempted misuse is auditable.
- [ ] **SA-10: Plugin, dependency, secret, and evidence integrity (needs proof).** Exercise manifest and generated-config validation, artifact digest/signature checks, dependency changes, secret redaction, and SQLite/evidence tampering using temporary fixtures. **Proof:** alter one artifact byte, introduce a canary secret, and corrupt one evidence record; confirm detection without leaking the canary into responses or logs. **Resolve:** pin and scan dependencies, verify artifacts before install, keep signing keys outside artifacts, use least-privilege file permissions, and make evidence tampering detectable. **Regression:** each tamper is denied and recorded while clean fixtures still pass.

## Evidence Log

The following probes ran on 2026-09-24 in WSL Docker against this checkout mounted read-only at `/app`. The image was the existing `vad-test:local` image (`sha256:821f7b234c58c38e4b9b1206bcc035747e8a9b96a3c05d311f906d3bb2a9c9a7`); it was not rebuilt. The control-plane demo and Level 3 UI ran in separate disposable containers with `--network none`, localhost binding, temporary SQLite state, and no host-published ports. `tests/security_probe_local.py` is the repeatable probe; it uses no live credentials, external service, or host workspace for mutation. Its output is summarized below. The script describes the vulnerable baseline and is not a passing security regression suite.

Reproduce from a WSL shell in the repository root with `vad-test:local` available. Run the probes promptly after startup; these are disposable containers, and the observed exit behavior is noted below.

```bash
docker run -d --rm --name vad-audit-control --network none --mount "type=bind,source=$PWD,target=/app,readonly" -e VAD_LIVE_SERVICES=disabled vad-test:local vad local-os demo --host 127.0.0.1 --port 8080 --db /tmp/vad-audit-control.sqlite3 --ui-root /tmp/vad-audit-ui --evidence-root /tmp/vad-audit-evidence --plugin-root /tmp/vad-audit-plugins
docker run -d --rm --name vad-audit-ui --network none --mount "type=bind,source=$PWD,target=/app,readonly" -e VAD_LIVE_SERVICES=disabled vad-test:local python -m vad.server.serve --host 127.0.0.1 --port 8080 --db /tmp/vad-audit-ui.sqlite3 --ui-root /tmp/vad-ui --evidence-root /tmp/vad-evidence --seed-level3-demo
docker exec vad-audit-control python tests/security_probe_local.py control
docker exec vad-audit-ui python tests/security_probe_local.py ui
docker stop vad-audit-control vad-audit-ui
```

| ID | Observed result | Remaining proof / proposed fix |
| --- | --- | --- |
| SA-01 | `POST /diff-proposals/{id}/apply` returned 200 with `applied=true` for two caller-supplied `allow=true` decisions. The caller-selected temporary workspace changed from `before` to `after`; the other temporary workspace stayed `before`. No stored approval was created. | Fix the authority and workspace binding described in SA-01; then require a denial and zero file changes in the same probe. |
| SA-02 | An unauthenticated request claiming a different `release_guardian` returned 201 with `decision.allow=true`; `GET /runs/{id}/approvals` confirmed the event persisted. | Authenticate the actor and bind the approval to run/evidence identity; test aliases and role spoofing. |
| SA-03 | `tools/list` hid `repo_patch` for an observer but exposed it when the same client claimed `role=builder` and `approved_high_risk_tools=[repo_patch]`; both calls returned 200. | A high-risk handler was deliberately not invoked. Fix connection-derived authority and test that forged arguments cannot invoke the handler. |
| SA-04 | An unauthenticated claimed operator created a high-risk operator-intent record: POST returned 201 and GET read it back with `role=operator`. | Test client registration, privileged events, leases, and work-item transitions separately; implement route-wide authentication. |
| SA-05 | The operator-intent write above and the forged release approval succeeded with untrusted `Origin` and `Host` headers. Unauthenticated GET of `/diff-proposals` returned 200 and included the probe proposal. | Non-local reachability and browser behavior were not tested because containers had no external network. Bind demo ports to loopback and add authentication, authorization, and browser-origin defenses. |
| SA-06 | `ExecutionGuard` allowed `python -c` and read a harmless sentinel outside its configured working directory (`exit_code=0`). | No timeout or resource-exhaustion probe was run. Move proof execution to an isolated, resource-limited sandbox. |
| SA-07 | A 131,072-byte patch body was accepted with 201 and read back intact with 200. This proves the absence of a cap below that size, not a denial-of-service exploit. | Add explicit body, patch, output, and concurrency limits; test rejection at the configured boundary. |
| SA-08 | A controlled failure on the second file write left the first temporary file changed (`after`) and the second unchanged (`before`). | Symlink-swap and race probes remain pending. Make multi-file apply atomic or reliably reversible. |
| SA-09 | No agent prompt-injection probe was run. | Use the deterministic agent fixture and keep authorization outside model-produced content. |
| SA-10 | No new tamper probe was run. Existing plugin audit tests were included in the focused gate below. | Add bounded artifact/secret/evidence tamper cases and verify independent detection. |

Focused gate run in the same read-only checkout: `python -m pytest -q -p no:cacheprovider tests/test_security_audit.py tests/test_local_hardening_0307.py tests/test_control_plane_plugins.py` yielded **34 passed, 1 failed**. The failing `test_security_audit_default_tests_only_use_local_network_endpoints` flags 37 non-local URL literals in other test files (first: `https://github.com` in `tests/test_connectors_admin.py`). That static string scan does not distinguish a fixture URL from an actual network call. Repair the gate with a runtime network block or a narrower check, then rerun it; do not count this gate as passing. The full suite was not run.

After the probes, the disposable containers exited with code 255 (`OOMKilled=false`) and no application traceback in the captured logs. The cause was not established. No host ports or live services were used. The exit behavior should be checked separately before using this setup as a long-running security test environment.

Do not mark any item complete from code inspection alone. Keep proof artifacts free of real secrets and redact paths or identities before sharing them.

## Out Of Scope

- Live cloud provider, paid model, KMS, or production deployment calls are not enabled by default, and no live credentials are required.
- The local HMAC signer is for development evidence only and is not a production key-management design.
- The hosted UI is a local dashboard/control surface over backend APIs; it is not an independent authorization boundary.

## Verification

Run the focused audit gate:

```powershell
wsl -e bash -lc "cd /mnt/c/LocalProjects/condensate-arch-docs/verified-agentic-development && docker build -t vad-test:local . && docker run --rm vad-test:local python -m pytest tests/test_security_audit.py"
```

Run the default completion gate:

```powershell
wsl -e bash -lc "cd /mnt/c/LocalProjects/condensate-arch-docs/verified-agentic-development && docker build -t vad-test:local . && docker run --rm vad-test:local"
```
