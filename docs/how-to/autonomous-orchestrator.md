# How-To: Run and Scale Parallel Swarms with the Autonomous Orchestrator

The **Autonomous Orchestrator** is VAD Enterprise's distributed task execution engine. It coordinates parallel swarms of autonomous agents working across complex multi-phase dependency graphs without git worktree isolation, guaranteeing deadlock-free execution, automatic crash recovery, and verifiable staged commits.

---

## 1. Architectural Philosophy: Why No Git Worktree Isolation?

In traditional agent systems, scaling parallel agents often relies on creating isolated `git worktree` directories for each worker. In large enterprise codebases, this strategy suffers from severe limitations:
- **Massive Storage Bloat:** Duplicating multi-gigabyte build artifacts, `node_modules`, virtual environments, and caches.
- **Git Lock Contention:** High-frequency git index updates and branch references contend for repository-level locks.
- **Merge Hell at Tranche Boundaries:** Complex, multi-branch reconciliation often creates silent merge collisions.

### The VAD Solution: In-Place Parallel Dispatch with File Boundaries
VAD orchestrates parallel workers **in the same working tree**:
1. **Topological Tranche Admission:** Tasks that touch overlapping file paths are sequenced across different tranches.
2. **File-Level Scope Enforcement:** Each parallel worker is granted exclusive write locks on disjoint sets of files (`spec.scope`).
3. **Atomic Staged Commits:** As each worker finishes and passes container verification, its changes are staged atomically.

```
       ┌────────────────────────────────────────────────────────┐
       │     Implementation Plan & Tracker State Table          │
       │    (planning_artifacts/implementation_tracker.md)      │
       └───────────────────────────┬────────────────────────────┘
                                   │
                                   ▼
       ┌────────────────────────────────────────────────────────┐
       │             Dependency Graph Engine (DAG)              │
       │    - Cycle Detection (Tarjan / DFS)                    │
       │    - Parallel Tranche Partitioning                     │
       └───────────────────────────┬────────────────────────────┘
                                   │
                                   ▼
       ┌────────────────────────────────────────────────────────┐
       │                   Admission Control                    │
       │    - Concurrency Throttling (max_concurrency)          │
       │    - Prerequisite Evidence Validation (evidence/<id>)  │
       │    - DoR Gate Evaluation                               │
       └───────────────────────────┬────────────────────────────┘
                                   │
                                   ▼
       ┌────────────────────────────────────────────────────────┐
       │              Parallel Dispatch Executor                │
       │   ┌────────────────┐ ┌────────────────┐ ┌────────────┐ │
       │   │ Worker Agent 1 │ │ Worker Agent 2 │ │ Worker N   │ │
       │   │ (vad/decision/)│ │ (vad/runners/) │ │ (vad/tui/) │ │
       │   └───────┬────────┘ └───────┬────────┘ └─────┬──────┘ │
       └───────────┼──────────────────┼────────────────┼────────┘
                   │                  │                │
                   ▼                  ▼                ▼
       ┌────────────────────────────────────────────────────────┐
       │              Heartbeat & Liveness Monitor              │
       │  - Lease Ping (every 60s)                              │
       │  - Auto-Reclamation of Stale Leases (>300s)           │
       │  - Crash Resumption from Last Checkpoint               │
       └───────────────────────────┬────────────────────────────┘
                                   │ (All Tranches Done)
                                   ▼
       ┌────────────────────────────────────────────────────────┐
       │                 Staged Commit Manager                  │
       │   - Verifies 100% Evidence Coverage                    │
       │   - Atomic Git Staging on Feature Branch               │
       └───────────────────────────┬────────────────────────────┘
                                   │
                                   ▼
       ┌────────────────────────────────────────────────────────┐
       │             Operator Confirmation Push Gate            │
       │       (Interactive TUI / 2-Person Authorization)       │
       └────────────────────────────────────────────────────────┘
```

---

## 2. Defining and Parsing Dependency Graphs

Tasks are defined in the project plan with declared dependencies. The graph engine converts them into a Directed Acyclic Graph (DAG) and computes execution tranches.

### Python API Example
```python
from vad.orchestrator.dep_graph import DependencyGraph, TaskNode

graph = DependencyGraph()

# Add tasks with prerequisites
graph.add_node(TaskNode("A1", description="DoR Gate", dependencies=[]))
graph.add_node(TaskNode("A2", description="Decision Catalog", dependencies=[]))
graph.add_node(TaskNode("A3", description="Lexical Router", dependencies=["A1", "A2"]))
graph.add_node(TaskNode("B1", description="DAG Engine", dependencies=[]))
graph.add_node(TaskNode("B2", description="Admission Control", dependencies=["B1"]))

# 1. Cycle Detection
if graph.has_cycle():
    raise ValueError(f"Circular dependency detected: {graph.find_cycles()}")

# 2. Compute Parallel Tranches
tranches = graph.compute_tranches()
for idx, tranche in enumerate(tranches):
    print(f"Tranche {idx}: {[task.id for task in tranche]}")

# Output:
# Tranche 0: ['A1', 'A2', 'B1'] (can run concurrently)
# Tranche 1: ['A3', 'B2']
```

---

## 3. Admission Control & Concurrency Management

The `AdmissionController` acts as a gatekeeper before any worker is launched:
- **Prerequisite Evidence Check:** Verifies that all upstream dependencies have generated valid, passing `evidence/<dep_id>.json`.
- **Concurrency Quota:** Limits concurrent active workers to `--max-concurrency` to prevent resource exhaustion.
- **DoR Verification:** Evaluates the Definition of Ready gate for each admitted task.

```python
from vad.orchestrator.admission import AdmissionController

admission = AdmissionController(max_concurrency=4, evidence_dir="evidence/")

# Evaluate if task A3 is ready for admission
is_admitted, reason = admission.can_admit("A3", graph=graph)
if not is_admitted:
    print(f"Task A3 held in queue: {reason}")
    # Reason: "Waiting for prerequisite A1 evidence: evidence/A1.json missing"
```

---

## 4. Heartbeat Monitoring & Dead-Agent Recovery

Autonomous agent workers can crash due to OOM, network disconnection, or process termination. VAD's leasing model guarantees zero deadlocks.

### The Leasing Protocol
1. **Acquiring a Lease:** When a worker begins task `B4`, it claims the task in `implementation_tracker.md` (`status: locked`, `agent_id: worker-1`, `locked_at: <timestamp>`).
2. **Heartbeat Pings:** The worker updates its `heartbeat` timestamp at least every 60 seconds.
3. **Sweeper Detection:** The orchestrator runs a background sweeper every 30 seconds.
4. **Automatic Reclamation:** If `current_time - heartbeat > timeout_seconds` (default: 300s):
   - The orchestrator marks the lease `stale`.
   - Revokes the lock and resets the task status to `ready`.
   - Logs an incident event in the audit ledger.
   - Dispatches a replacement worker to resume the task.

```python
from vad.orchestrator.heartbeat import HeartbeatMonitor

monitor = HeartbeatMonitor(lease_timeout_seconds=300)

# Sweeper execution
stale_leases = monitor.sweep()
for lease in stale_leases:
    print(f"Reclaimed abandoned task {lease.task_id} from dead agent {lease.agent_id}")
    monitor.reassign_task(lease.task_id)
```

---

## 5. Parallel Dispatch Pool Execution

The orchestrator executes admitted tasks using worker adapters (`vad/runners/`):
- **Subprocess Pool:** Each agent runs in its own process with dedicated stdin/stdout pipes.
- **Docker Test Isolation:** When the agent runs tests, it delegates execution to disposable Docker containers on WSL (`vad-runner:3.11`).
- **File System Cleanliness:** Only files in `planning_artifacts/` and `evidence/` are updated during tracking, and both are kept gitignored.

```python
from vad.orchestrator.parallel_dispatch import ParallelDispatchPool

pool = ParallelDispatchPool(max_workers=4)

def run_task_worker(task_id: str):
    # Runs the dedicated model adapter (Claude, Cursor, Copilot)
    runner = get_runner_for_task(task_id)
    return runner.execute(task_id)

futures = {pool.submit(run_task_worker, task.id): task.id for task in admitted_tasks}
```

---

## 6. Staged Commits & Operator Push Gate

Once all tasks across all tranches reach `done`:
1. **Evidence Verification:** The orchestrator scans `evidence/` to verify that all task IDs have valid passing JSON evidence.
2. **Staging:** Changes are staged using `git add`:
   ```python
   from vad.orchestrator.staged_commit import StagedCommitManager
   
   manager = StagedCommitManager(repo_root=".")
   staged_files = manager.stage_completed_work(tracker_path="planning_artifacts/implementation_tracker.md")
   ```
3. **Interactive Confirmation Gate:** The orchestrator does NOT push directly. It invokes the confirmation gate:
   ```python
   from vad.orchestrator.confirm_push import ConfirmPushGate
   
   gate = ConfirmPushGate(interactive=True)
   approved = gate.request_operator_confirmation(staged_files)
   if approved:
       manager.commit_and_push(branch="feature/vad-enterprise")
   ```

---

## 7. Complete CLI Reference

Launch the orchestrator from your terminal:
```bash
python -m vad.orchestrator.cli   --manifest planning_artifacts/implementation_plan.md   --tracker planning_artifacts/implementation_tracker.md   --max-concurrency 4   --heartbeat-timeout 300   --staged-commit   --auto-recover
```

### CLI Options
| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--manifest` | Path | Required | Path to markdown implementation plan with task definitions. |
| `--tracker` | Path | Required | Path to markdown state table (`implementation_tracker.md`). |
| `--max-concurrency` | Int | `4` | Maximum parallel agents running concurrently. |
| `--heartbeat-timeout`| Int | `300` | Seconds before considering an agent crashed. |
| `--staged-commit` | Flag | `False` | Automatically stage verified files upon tranche completion. |
| `--auto-recover` | Flag | `True` | Automatically reassign tasks from dead agents. |
| `--dry-run` | Flag | `False` | Parse DAG and print tranches without executing agents. |

---

## 8. Swarm Scaling Best Practices

1. **Keep Tranches Granular:** Limit task size to 1–3 files per task so smaller models can execute them reliably without context exhaustion.
2. **Strict File Boundaries:** Ensure tasks in the same tranche touch distinct directories to eliminate file overwrite races.
3. **Monitor via Operator TUI:** Open `python -m vad.ui.tui.app` in a separate terminal window to view real-time DAG node state transitions.
