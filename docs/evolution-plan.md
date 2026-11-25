# VAD Evolution Plan

This plan describes how to evolve VAD from the current local Level 4 reference
implementation into an automated agentic central orchestrator OS. It is written
as current-state architecture guidance, not as historical delivery notes.

## Current State

VAD currently provides a local-first agentic operating-system reference:

- a localhost control-plane server with lifecycle, readiness, and local-only
  binding defaults;
- SQLite-backed evidence for runs, approvals, control-plane events, client
  manifests, heartbeats, stale detection, task leases, plugin status, and
  dashboard replay;
- governed MCP exposure over stdio and a local HTTP JSON-RPC route, with
  role-aware tool visibility, high-risk tool denial, client attribution, and
  redacted event summaries;
- package and plugin artifacts for Codex, Claude Code, VS Code, Cursor,
  Windsurf, OpenCode, Generic MCP/A2A, and the Antigravity generic fallback;
- review-only plugin install dry-runs, artifact digests, local development
  signatures, reproducible artifact reports, and publication decision records;
- a browser dashboard that reconstructs activity from the event ledger and
  displays clients, leases, task state, proof status, terminal status, and
  plugin readiness;
- deterministic simulator, role-separation, compose, and Docker verification
  paths that run without live credentials, paid model calls, or cloud services.

The current design is intentionally local and operator-owned. It is not hosted
SaaS, managed tenancy, a cloud dashboard, remote MCP hosting, production key
management, automatic package publication, automatic plugin installation, or
live production deployment.

## Central Orchestrator OS Gap Closure

The next architecture step is to make the local control plane an active
orchestrator instead of mainly a ledger, policy boundary, package generator,
and dashboard replay surface. The following gaps are the recommended closure
order.

1. Durable Work Queue And Scheduler

   Add a first-class work queue that can create, prioritize, assign, pause,
   resume, and cancel tasks. The scheduler should use client manifests,
   heartbeats, roles, capabilities, trust state, active leases, and policy
   decisions to select an owner. Assignment must append events before work is
   exposed to a client, so every scheduler decision is replayable.

2. Lease Recovery And Reassignment

   Extend stale scans and lease expiry into orchestrated recovery. When a
   client becomes stale, VAD should move affected work into a recoverable state,
   preserve the previous owner and evidence links, and requeue or reassign the
   work only through a policy-checked transition. Recovery should never erase
   abandoned work from the event ledger.

3. Run And Task State Machine

   Define explicit run and task states for planned, assigned, running, blocked,
   waiting-for-human, verifying, approved, failed, cancelled, and completed
   work. The dashboard projection should become a view over that state machine,
   while the event ledger remains the source of truth.

4. Live Local Client Connectors

   Convert package artifacts into live local connector contracts. Each connector
   should register, heartbeat, receive assigned work, stream status, emit tool
   and proof events, and surface human review requests without requiring VAD to
   trust the client process blindly. Codex, Claude Code, VS Code, Cursor,
   Windsurf, OpenCode, Generic MCP/A2A, and Antigravity fallback behavior should
   share the same connector protocol where their host surfaces allow it.

5. Persistent Plugin Inventory

   Move beyond deterministic plugin status seeds and dry-run previews by adding
   a persistent plugin inventory. The inventory should record review state,
   applied config hashes, backup paths, uninstall status, rollback results, and
   event-derived dashboard state. Apply, uninstall, and rollback writers should
   remain local, explicit, reviewable, and policy-gated.

6. Policy, Budget, And MEES Governance

   Promote local policy from point checks into a durable governance layer. Tool
   grants, release approvals, token budgets, effort scoring, MEES-style
   evidence requirements, workspace scope, and live-service opt-ins should be
   represented as auditable records. High-risk actions should require both
   policy allowance and current operator intent.

7. Terminal And Proof Streaming

   Add structured streaming for command output, proof progress, test gates, and
   recovery actions. Raw logs should stay local and redacted summaries should be
   projected to the dashboard. Streaming events should link to run evidence and
   support replay without reading arbitrary terminal files.

8. Diff Proposal And Apply Workflow

   Model file changes as proposals before mutation. Builders should submit
   diffs with intent, affected paths, risk, tests, and rollback notes. Verifiers
   and release guardians should be able to approve or reject the proposal.
   Applying a diff should use the existing workspace sandbox and append
   before/after evidence.

9. Operator Command Surface

   Add command/control APIs and CLI verbs for assignment, pause/resume,
   cancellation, reassignment, plugin inventory decisions, proof reruns,
   recovery actions, and local health audits. Every operator command should have
   a dashboard-visible event and a deterministic test path.

10. Secrets And Signing Hardening

   Keep secrets out of manifests, events, and configs. Store only secret
   references and add explicit validation for supported reference types. Keep
   the current HMAC signer as local development evidence, then introduce
   production signing adapters only behind opt-in tests and documented operator
   approval.

11. Reference Architecture Documentation

   Keep `docs/` focused on the current implemented solution, current
   limitations, and current recommended evolution. Historical trackers can
   remain at the workspace root, but architecture docs should avoid rollout
   vocabulary and should state whether a capability is implemented, remaining
   local hardening, or future cloud/SaaS scope.

## Recommended Implementation Tracks

Track A, Orchestrator Core: durable work queue, scheduler, run/task state
machine, lease recovery, reassignment, and dashboard projection updates.

Track B, Client Runtime: live local connector contract, per-client work intake,
heartbeat/status streaming, tool/proof event emission, and operator review
requests.

Track C, Governance: persistent grants, release approvals, MEES and effort
evidence, token budgets, live-service opt-ins, policy event replay, and
operator command audits.

Track D, Plugin Operations: persistent plugin inventory, apply/uninstall/
rollback writers, event-derived plugin dashboard state, and local artifact
health checks.

Track E, Proof And Change Control: terminal/proof streaming, diff proposal
records, sandboxed apply, verifier feedback, release-guardian approval, and
rollback evidence.

Track F, Documentation And Packaging: keep docs current-state aligned, preserve
local install and publication boundaries, and add tests that prevent historical
rollout language from returning to `docs/`.

## Next Tracker Recommendation

Start the next tracker with Track A. The smallest complete slice is:

- add a SQLite-backed `work_items` table and service;
- add scheduler decisions that assign work to registered active clients;
- append assignment, blocked, completed, failed, and reassignment events;
- project queued and assigned work into the dashboard task board;
- add CLI and local API verbs for create, assign, list, block, complete, and
  cancel;
- add recovery behavior that requeues or marks work after stale-client lease
  expiry;
- verify through Docker with focused scheduler, lease-recovery, dashboard, and
  docs tests.

That slice closes the largest central-orchestrator gap while reusing the
current SQLite ledger, client registry, policy checks, task leases, dashboard
projection, and WSL/Docker verification boundary.

---

## Next-Stage Architecture: Evidence-Driven Autonomy & Memory-Agnostic Control Plane

With the local central-orchestrator baseline (durable `work_items`, scheduler,
run/task state machine, plugin inventory and writers, diff proposals, operator
intent persistence, and stale-client recovery) in place, VAD's next evolution
focuses on three priorities and one end-to-end reference milestone.

### Three-Layer Independence

VAD governs agent execution hosts and consumes interchangeable memory providers
without depending on any specific model vendor, host runtime, or memory engine:

- **Agent execution hosts:** Codex, Claude Code, Cursor, VS Code, Windsurf,
  OpenCode, Generic MCP/A2A, and custom domain agents. VAD governs them rather
  than competing for model execution.
- **VAD control plane (authoritative):** intent management (EIPs), agent
  orchestration, proof obligations, independent verification, policy
  enforcement, release governance, authoritative evidence ledger, and durable
  execution state.
- **Memory Provider Interface (interchangeable, advisory only):** versioned
  contracts, capability discovery, configurable transport (`http`, `mcp`, or
  `both`), and conformance tests supporting `Null`, `Local`, `Condensate Core`
  (reference implementation with advanced optional capabilities), `Mem0`,
  `Graphiti`, and arbitrary external HTTP/MCP memory target endpoints.

### Authoritative Evidence vs. Interchangeable Memory

VAD remains the sole authority for verification and governance:

- **VAD-owned (non-interchangeable):** Executable Intent Packages, proof
  obligations, verification results, policy decisions, approval records,
  execution state, and the authoritative evidence ledger.
- **Memory-provider-owned (interchangeable, advisory):** historical knowledge,
  semantic retrieval, inferred lessons learned, and context consolidation.

A memory provider cannot alter a verification result, grant an approval, or
modify the authoritative evidence ledger. VAD operates cleanly without optional
memory capabilities or external memory services.

### Priority 1: Measurable Verification & Evidence-Driven Autonomy

- Upgrade EIP contracts with `critical` risk tier, explicit assurance levels
  (`A1_BASIC` through `A4_RESTRICTED`), and evidence freshness policies
  (commit/tree pinning, environment digests, and expiry TTLs).
- Enforce independent isolated verification so builders never self-verify or
  self-approve, replacing default hardcoded HMAC signing with auto-generated
  local Ed25519 keypairs behind a pluggable key provider.
- Replace static automation tiers with a Risk × Evidence Autonomy Matrix
  evaluated across `build`, `ci_cd`, `deploy`, `runtime`, and `drift` stages:
  - `low`: autonomous merge after checks;
  - `medium`: independent isolated verification required;
  - `high`: independent verification plus human approval required;
  - `critical`: restricted execution with explicit operator authorization and
    dual sign-off.
- Measure operational outcomes: autonomous safe pass-through rate, escaped
  defects, verification false positives, recovery success rate, human review
  time, and cost per accepted change.

### Priority 2: Closed Learning Loop via Memory Provider Interface

- Keep **observed facts** (immutable projections linked to authoritative
  evidence digests) strictly separate from **inferred lessons** (candidate
  recommendations requiring explicit human review before promotion to policy or
  default proof obligations).
- Publish verified outcomes, rejected changes, and incident facts to the
  configured memory provider, and retrieve advisory context and approved
  lessons during intent assessment and proof planning.

### Priority 3: Reliable Cross-Host Orchestration

- Preserve the local-first model while adding monotonic lease fencing tokens,
  persisted idempotency keys, durable stage checkpoints, scoped cross-host role
  isolation tokens, and crash/host-failure recovery.
- Introduce optional operator-hosted remote workers and enterprise controls as
  opt-in contracts without requiring hosted SaaS.

### Reference Milestone: Verified Delivery Flow

1. Receive a change request and create an EIP (enriched with advisory history).
2. Map requirements and approved lessons to executable proof obligations.
3. Assign construction to an agent host with fenced lease and checkpoints.
4. Run independent verification in an isolated environment with freshness checks.
5. Evaluate policy and autonomy rules to decide required approvals.
6. Release the approved change with a linked authoritative evidence record.
7. Publish observed facts and candidate lessons to the memory provider.
8. Retrieve approved lessons when a similar task arrives.

The milestone succeeds when this flow completes across all risk tiers, survives
injected host/server/drift/memory failures, and passes independent operator
audit verification from the authoritative ledger alone.

### Platform Trajectory & Hierarchical Intent Investigation

- **Evolution stages:** (01) Verified agentic development → (02) Verified
  software delivery → (03) Autonomous software engineering → (04) Continuous
  verified operations (incident-triggered EIP lifecycle).
- **Platform scope:** Deliver Verified Software Delivery (Scope A) first while
  keeping core contracts decoupled behind domain adapter seams so general
  autonomous agent control (Scope B: operational, infrastructure, business, and
  compliance agents) extends cleanly without core rewrites.
- **Next design investigation:** Evaluate a Hierarchical Intent Model
  (`Strategic Objective → Programme → Engineering Objective → EIP → Task → Proof Obligation → Evidence`)
  keeping `EIP` as the atomic verification contract while enabling multi-EIP
  programme decomposition and rollup verification.
- **Implemented Stage 03–04 & Scope B Foundations (SQLite Schema v13):**
  - **Hierarchical Intent & Multi-EIP Delivery:** `IntentHierarchyService`,
    `HierarchicalDeliveryWorkflow`, and `verify_objective_audit_trail` govern
    `StrategicObjective → Programme → EngineeringObjective → ChildEIPRef`
    chains with non-downgradable floors, budget ceilings, topological parallel
    tranches, and coordinated release/rollback (`vad hierarchy`,
    `vad audit verify-objective`).
  - **Scope B Built-In Domain Packs:** Built-in `Infrastructure`, `Operations`,
    `Business`, and `Compliance` domain packs execute through
    `DomainPackExecutionRunner` (`vad domain-pack list`,
    `vad domain-pack evaluate`) reusing `ReleaseDecisionEngine` and Ed25519
    DSSE attestations without modifying core packages.
  - **Opt-In Remote Workers & Enterprise RBAC:** `RemoteWorkerCoordinator`
    preserves `LOCAL_FIRST` (`remote_workers_enabled = False`) by default while
    supporting opt-in mTLS/OIDC worker pools, fenced `HostSessionToken`
    issuance, constant-time SHA-256 `ArtifactTransferManifest` bundle checks,
    and enterprise RBAC release-guardian group co-authorization
    (`vad remote-workers list`).
  - **Stage 04 Continuous Verified Operations:**
    `ContinuousVerifiedOperationsWorkflow` connects runtime/drift incidents to
    `IncidentResponder`, `PostMortemGenerator`, `DefectLinkRecord`,
    `EIPSeeder`, `ObservedFact` + candidate `InferredLesson`, and governed
    remediation delivery (`vad operations remediate`).

