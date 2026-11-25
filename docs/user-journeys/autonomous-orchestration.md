# User Journey: Autonomous Swarm Orchestration

This journey details how a complex, multi-phase feature is orchestrated across a swarm of parallel agents without git worktree isolation, using the single source of truth (`implementation_plan.md` and `implementation_tracker.md`).

---

## 1. Preparing the Implementation Plan

The lead engineer or architect authors:
1. `planning_artifacts/implementation_plan.md`: Outlines the technical architecture, component breakdown, and individual task items (e.g. A1, A2, B1...).
2. `planning_artifacts/implementation_tracker.md`: The live state table tracking task states (`ready`, `locked`, `done`, `failed`), assigned agent IDs, heartbeats, and completion timestamps.

---

## 2. Launching the Autonomous Orchestrator

The operator starts the autonomous orchestrator:
```bash
python -m vad.orchestrator.cli   --manifest planning_artifacts/implementation_plan.md   --tracker planning_artifacts/implementation_tracker.md   --max-concurrency 4   --heartbeat-timeout 300
```

### Execution Lifecycle:
1. **DAG Parsing:** The orchestrator reads task dependencies and organizes them into parallel tranches.
2. **Admission Control:** Checks system resource quotas and verifies that preceding dependencies have generated valid evidence before admitting new tasks.
3. **Parallel Dispatch:** Launches worker agents across tasks in the current tranche. Agents work in parallel on the same workspace using file-level boundaries without git worktree collisions.

---

## 3. Resilience and Automatic Crash Recovery

During a long-running swarm execution:
- Every active agent pings `heartbeat.ping(task_id)` every 60 seconds.
- If an agent's container crashes or is abruptly killed, the heartbeat monitor detects the timeout (>300s).
- The task lock is automatically released and marked `ready`.
- The orchestrator dispatches a replacement agent to resume the task from the last valid checkpoint.

### Protection Against Stale ("Zombie") Agents
Recovery has to be safe even if the crashed agent comes back.

- **Fence tokens.** Each assignment of a work item carries a `fence_token` that only goes up (1, 2, 3...). When a task is reassigned after a crash, the new owner gets the next number. If the original agent wakes up and writes with its old token, VAD rejects it with `409 Conflict`, records the rejection, and leaves the work item untouched.
- **Idempotent retries.** If a host retries a call with the same `idempotency_key`, VAD replays the original result. The same key with a different payload is refused. No duplicate events or transitions are created.
- **Durable checkpoints.** Progress is saved in SQLite at each delivery stage (`eip_created`, `proof_planned`, `construction_assigned`, `diff_proposed`, `verified`, `policy_decided`, `released`, `memory_synced`). If the control plane itself restarts, VAD reads the latest checkpoint and resumes at the next stage instead of repeating completed work.
- **Role-scoped sessions.** Each host receives a signed, short-lived session token bound to its client, role, work item, and fence token. A builder session cannot call verifier or release tools, and a stale token is refused.

You can inspect recovery state:
```bash
# Stages reached by a run
curl http://127.0.0.1:8080/checkpoints/<run_id>
```

Recovery success is tracked as a metric (`recovery_success_rate` in `vad metrics autonomy`).

---

## 4. Staged Commit & Verification Gate

When all tranches complete:
- The orchestrator validates that every task has generated `evidence/<ID>.json`.
- Changes are staged cleanly on the feature branch.
- Independent verification and the risk-based policy decision must succeed before release. Low and Medium risk changes can proceed autonomously once independently verified; High and Critical changes wait for human approval (see [Verified Delivery Flow](verified-delivery.md)).
- The orchestrator pauses at the **Staged Commit Gate** for human confirmation before any push occurs.

---

## 5. Stage 03 Multi-EIP Hierarchical Programme Delivery (`HierarchicalDeliveryWorkflow`)

For large engineering initiatives spanning multiple services or modules, VAD decomposes intent above the atomic `EIP` boundary (`StrategicObjective -> Programme -> EngineeringObjective -> ChildEIPRef`) using [`IntentHierarchyService`](../../vad/control_plane/intent_hierarchy_service.py) and [`HierarchicalDeliveryWorkflow`](../../vad/workflows/hierarchical_delivery.py) (see [RFC: Hierarchical Intent Model](../architecture/rfc-hierarchical-intent.md)):

1. **Non-Downgradable Floor & Budget Registration:** `StrategicObjective`, `Programme`, and `EngineeringObjective` nodes are persisted in SQLite Schema v13 (`intent_hierarchy_nodes`). Every `ChildEIPRef` leaf must meet or exceed parent `risk_floor`, `assurance_floor`, and `inherited_invariants`, stay acyclic across `depends_on`, and stay within `max_total_cost_usd`.
2. **Pre-Dispatch Decomposition Approval:** Before any child `EIP` executes, an authorized guardian approves the decomposition:
   ```bash
   vad hierarchy list --db .vad/control-plane/vad.sqlite3 --level engineering_objective
   vad hierarchy approve eo-gateway-mtls --approver security-owner --role release_guardian --db .vad/control-plane/vad.sqlite3
   ```
   (Equivalent HTTP routes: `POST /intent-hierarchy/nodes`, `GET /intent-hierarchy/nodes`, and `POST /intent-hierarchy/objectives/{objective_id}/approve`.)
3. **Topological Tranche Execution & Downstream Halt:** `HierarchicalDeliveryWorkflow.execute(...)` plans parallel DAG layers (`TrancheSchedulePlan`) and dispatches each eligible `ChildEIPRef` through `VerifiedDeliveryWorkflow`. If any upstream leaf fails verification, exceeds budget, or loses freshness (`evidence_fresh=False`), transitive downstream dependents are automatically blocked (`skipped_blocked_eips`).
4. **Cross-EIP Integration Proof & Coordinated Release:** Once all leaves pass with fresh evidence, cross-`EIP` integration proofs run and `evaluate_release_readiness` computes `ObjectiveReleaseReadiness` (`releasable_child_eips`, `held_in_staging_eips`, `rollback_required_eips`):
   ```bash
   vad hierarchy rollup eo-gateway-mtls --db .vad/control-plane/vad.sqlite3
   ```
   (Equivalent HTTP route: `GET /intent-hierarchy/objectives/{objective_id}/rollup`.)

---

## 6. Opt-In Operator-Hosted Remote Workers (`RemoteWorkerCoordinator`)

VAD runs in `WorkerExecutionMode.LOCAL_FIRST` by default (`remote_workers_enabled=False`) with zero mandatory hosted SaaS. Operators running self-hosted build farms or isolated verifier pools can explicitly opt in to remote execution via [`RemoteWorkerCoordinator`](../../vad/control_plane/remote_worker_coordinator.py) (see [Opt-In Operator-Hosted Remote Workers](../architecture/remote-workers.md)):

1. **mTLS / OIDC Admission:** Workers register with `RemoteWorkerIdentity` (`WorkerAuthMethod.MTLS` or `WorkerAuthMethod.OIDC`, `https://` endpoint, and explicit `operator_intent_ref`):
   ```bash
   vad remote-workers list --db .vad/control-plane/vad.sqlite3
   ```
   (HTTP: `GET /remote-workers` and `POST /remote-workers/admit`.)
2. **Fenced Dispatch & Scoped Session:** `RemoteWorkerCoordinator.dispatch_work(...)` authorizes `RemoteDispatchEnvelope` (checking role, allowed risk tiers, monotonic `fence_token`, and `HostRole.VERIFIER != builder_actor`), verifies input payloads against `ArtifactTransferManifest.verify_bundle`, and issues a scoped 1-hour `HostSessionToken`.
3. **Content-Addressed Receipts & Audit Linkage:** Workers return a fenced `RemoteExecutionReceipt` verified by `ingest_execution_receipt(...)`; manifests are persisted in Schema v13 (`artifact_transfer_manifests`, `GET/POST /remote-workers/manifests`) and checked independently by `verify_run_audit_trail`.
