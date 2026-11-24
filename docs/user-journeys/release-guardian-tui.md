# User Journey: Operator Monitoring & Release Guardian in the Textual TUI

This guide details how a **Platform Operator** or **Release Guardian** supervises autonomous agent swarms, monitors routing calibration, inspects token spend, and enforces the enterprise **2-person release approval rule** using the interactive **Textual Terminal UI (TUI)**.

---

## 1. Persona & Governance Responsibilities

In high-assurance enterprise software engineering, autonomous agents are granted significant latitude to plan, build, and test software. However, production promotion requires human accountability.

| Policy Objective | Operator / Release Guardian Role |
|------------------|----------------------------------|
| **Human in the Loop** | Supervises live execution without blocking autonomous tranches. |
| **Separation of Duties** | Enforces the 2-person rule: an agent that authors code cannot approve its own release. |
| **Calibration Oversight** | Intercepts low-confidence or anomalous routing decisions before execution. |
| **Budget Enforcement** | Monitors token burn rates and triggers emergency stops if runaway loops occur. |

---

## 2. Launching and Navigating the TUI

The Operator TUI is built on Python's **Textual** framework, providing a high-performance, terminal-native dashboard that runs over SSH or inside local terminal windows.

### Launching
```bash
python -m vad.ui.tui.app
```

```
┌─ VAD Enterprise Operator TUI ──────────────────────────────────────────────┐
│ [1] Decisions  [2] Dependency Graph  [3] Analytics  [4] Staged Confirmation│
├────────────────────────────────────────────────────────────────────────────┤
│                                                                            │
│                           ACTIVE PANEL CONTENT                             │
│                                                                            │
├────────────────────────────────────────────────────────────────────────────┤
│ Active Agents: 4 | Completed: 67/71 | ECE: 0.042 | Budget: $12.40 / $50.00 │
└─ [1-4]: Switch Tab | [r]: Refresh | [c]: Confirm | [q]: Quit ──────────────┘
```

### Global Keybindings
- `1`: Switch to **Decisions Panel**
- `2`: Switch to **Dependency Graph Panel**
- `3`: Switch to **Analytics Panel**
- `4`: Switch to **Confirmation Panel**
- `r`: Force manual poll/refresh of state tables
- `q` or `Ctrl+C`: Gracefully detach and exit TUI

---

## 3. Panel 1: Decisions Panel — Live Routing & Calibration

The Decisions Panel (`vad/ui/tui/decisions_panel.py`) provides real-time visibility into the Decision Engine:

### Key Features
1. **Live Ask Routing Stream:** Displays incoming engineering asks, their classified intent, and the assigned agent harness (Claude Code, Cursor, Copilot, Codex, or Aider).
2. **Scoring Breakdown:**
   - **Lexical Score:** BM25 keyword relevance to historical task descriptors.
   - **Semantic Score:** Vector cosine similarity using code embeddings.
   - **Hybrid RRF Score:** Reciprocal Rank Fusion combined confidence.
3. **Expected Calibration Error (ECE) Gauge:**
   - Visualizes reliability bins ($P(	ext{accuracy} \mid 	ext{confidence})$).
   - Alerts the operator when the model's confidence deviates from empirical correctness by $>10\%$.
4. **Manual Override:**
   - If an ask scores below the routing threshold (e.g. 0.75), it highlights in amber: `NEEDS_OPERATOR_ROUTING`.
   - The operator can manually assign a runner harness directly from the keyboard.

---

## 4. Panel 2: Dependency Graph Panel — Swarm Visualizer

The Dependency Graph Panel (`vad/ui/tui/dep_graph_panel.py`) renders the live execution DAG:

### Visual States of DAG Nodes
- 🟢 **DONE (Green):** Task finished, container test passed, evidence file recorded (`evidence/<ID>.json`).
- 🟡 **LOCKED / RUNNING (Yellow):** Worker agent actively executing with healthy heartbeat pings.
- ⚪ **PENDING (Dimmed):** Task waiting on upstream dependencies to complete.
- 🔴 **FAILED / RECOVERING (Red):** Agent died or tests failed; sweeper is executing crash recovery.

### Operator Actions
- **Highlight Task:** Press `Arrow Keys` to select any node.
- **Inspect Logs:** Press `Enter` to display the agent's real-time stdout/stderr log stream.
- **Cancel Task:** Press `x` to revoke the task lease and re-queue it.

---

## 5. Panel 3: Analytics Panel — Cost & Token Budgeting

The Analytics Panel (`vad/ui/tui/analytics_panel.py`) safeguards financial and computational boundaries:

### Metrics Monitored
- **Cumulative Cost vs. Autonomy Cap:** Visual progress bar tracking cumulative API costs against `--max-budget` (e.g. `$50.00`).
- **Token Mix:** Percentage of tokens allocated to Frontier Models (Claude 3.5 Sonnet, GPT-4o) vs. Commodity Models (Claude 3.5 Haiku, local Ollama models).
- **Execution Latency Percentiles:** Real-time histogram showing $p_{50}, p_{90}, p_{99}$ response times across runner harnesses.
- **Circuit Breaker Status:** If token burn rate exceeds threshold (e.g. 100k tokens/min), the TUI triggers an automated freeze.

---

## 6. Panel 4: Staged Confirmation & The 2-Person Rule

The Confirmation Panel (`vad/ui/tui/confirmation_panel.py`) is the final gate before any autonomous code is pushed to remote branches or deployed.

### Step-by-Step Release Promotion Flow

#### Step 1: Review the Staged Candidate
Press `4` to view the list of completed tranches awaiting promotion. The screen displays:
- Git branch name
- List of modified and added files
- Complete unified diff
- Cryptographic DSSE attestation verification status

#### Step 2: Verification of Independent Review
The panel verifies that:
1. The builder agent was independent of the verifier agent (`builder != verifier`).
2. The DSSE envelope has a valid signature matching an approved key in `schemas/integrity_registry.json`.
3. All containerized tests passed cleanly in throwaway Docker.

#### Step 3: Attempting Prohibited Self-Approval
If an agent attempts to call the approval endpoint for its own run:
```bash
POST /actions/approve
{
  "run_id": "run-4521",
  "actor": "claude-code-builder",
  "action": "approve_release"
}
```
The backend policy engine rejects the request with HTTP `403 Forbidden`:
```json
{
  "allow": false,
  "denials": ["builder may not approve own run"],
  "requires_human": true
}
```

#### Step 4: Operator Authorization
The human Release Guardian enters their authorized credentials in the TUI:
```
┌─ Release Promotion Approval ───────────────────────────────────────────────┐
│ Run ID:       run-4521                                                     │
│ Builder:      claude_code-builder-1                                        │
│ Verifier:     cursor-verifier-2 (DSSE Verified ✓)                          │
│ Operator ID:  release-guardian-neeraj                                      │
│ Reason:       Verified SAML integration and all 733 container tests pass.  │
├────────────────────────────────────────────────────────────────────────────┤
│ [Enter]: Confirm Release Promotion | [Esc]: Abort & Reject                 │
└────────────────────────────────────────────────────────────────────────────┘
```
Upon pressing `Enter`:
1. VAD verifies that `operator != builder`.
2. Appends an immutable `ApprovalEvent` to `vad.sqlite3`.
3. Issues the authenticated Git push to remote.

---

## 7. What Needs Your Approval: Risk-Based Authority

You are not asked to approve everything. VAD decides what needs a human based on risk and the proof available.

| Risk | Example | Your involvement |
|------|---------|------------------|
| **Low** | Documentation or isolated test update | None. Released autonomously after checks. |
| **Medium** | Internal API change | None, provided an independent verifier passed on fresh evidence. |
| **High** | Authentication or data migration | Required. The change waits as `human_approval_required` until you approve. |
| **Critical** | Production access or policy change | Required. Needs an explicit authorization reference and two distinct human approvers (`restricted_authorization_required`). |

Before approving, check the release decision for the run:
```bash
curl http://127.0.0.1:8080/release-decisions?run_id=run-4521
```
Look for: the assurance achieved versus required, whether evidence is fresh, who the builder and verifier were, and the recorded approvers. VAD will not let you approve if you are the builder, and a decision made on stale evidence is blocked until verification is re-run.

---

## 8. Reviewing Candidate Lessons

After runs complete, VAD may propose lessons (for example, "add a token-expiry proof obligation for auth changes"). These have no effect until you approve them.

```bash
vad lessons list --db .vad/control-plane/vad.sqlite3 --status proposed
vad lessons approve <lesson_id> --reviewer release-guardian-neeraj --role release_guardian --note "Confirmed against run-4521"
vad lessons reject <lesson_id> --reviewer release-guardian-neeraj --role release_guardian --reason "Too broad"
```

Approve only lessons you would be comfortable applying automatically to future similar changes. See [Learning Loop and Memory](learning-loop-and-memory.md).

---

## 9. Keeping an Eye on Outcomes

```bash
vad metrics autonomy --db .vad/control-plane/vad.sqlite3
```

Review these regularly to decide whether autonomy is being granted safely:

- autonomous safe pass-through rate
- escaped defects
- verification false positives
- recovery success rate
- human review time (median and 95th percentile)
- cost per accepted change
