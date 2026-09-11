# Verified Agentic Development (VAD) Enterprise — Quickstart Guide

Welcome to the **Verified Agentic Development (VAD) Enterprise** reference platform. VAD provides an end-to-end, mathematically governed, multi-harness development system where autonomous and human agents collaborate under strict verification, cryptographic attestation, and real-time policy gates.

---

## 1. System Requirements & Architecture

VAD Enterprise is OS-agnostic (pure Python 3.10+) and runs natively on Linux, macOS, and Windows (including WSL2).

### Prerequisites
- **Python 3.10+** (Python 3.11 recommended)
- **Open Policy Agent (OPA)** (for Rego policy compilation & evaluation)
- **Docker** (for throwaway, isolated test and build environments)

### Core Architectural Layers
```
┌──────────────────────────────────────────────────────────────────┐
│                     Operator TUI & Dashboard                     │
│           (Textual Terminal UI / Web Control Plane)              │
├──────────────────────────────────────────────────────────────────┤
│             Decision Engine & Autonomous Orchestrator            │
│ (DoR Gates | Hybrid Routers | Dep-Graph DAG | Parallel Dispatch) │
├──────────────────────────────────────────────────────────────────┤
│                     Cross-Harness Review                         │
│     (Claude | Cursor | Copilot | Codex | Aider | DSSE Signatures)│
├──────────────────────────────────────────────────────────────────┤
│                   Declarative Governance Layer                   │
│   (Quality Gates | Action Governance | LLM Hooks | Scope Guard)  │
├──────────────────────────────────────────────────────────────────┤
│                 Memory, Integrity & Compliance                   │
│     (SkillGuard Registry | Skill Ledger | SOC2/ISO Reports)      │
└──────────────────────────────────────────────────────────────────┘
```

---

## 2. 5-Minute Fast Track

### Step 1: Environment Setup
Inside your virtual environment or container:
```bash
pip install -e .
```

To run in disposable Docker on WSL without host pollution:
```bash
wsl docker run --rm -it -v /mnt/c/LocalProjects/condensate:/workspace -w /workspace vad-runner:3.11 bash
```

### Step 2: Validate an Executable Intent Package (EIP)
EIPs define the cryptographic contract of work to be performed:
```bash
vad eip validate examples/eip/sample.yaml
```

### Step 3: Launch the Operator TUI
Launch the interactive terminal dashboard to monitor active agents, decision catalog routing, dependency graphs, and token budgets:
```bash
python -m vad.ui.tui.app
```
*Key controls in TUI:*
- `1` - Decisions Panel (live routing & ECE calibration)
- `2` - Dependency Graph Panel (DAG status & parallel tranches)
- `3` - Analytics Panel (token consumption, latency, cost)
- `4` - Confirmation Panel (staged commits & push approvals)
- `q` - Exit TUI

### Step 4: Start the Control Plane Server
Run the local HTTP API and web dashboard:
```bash
python -m vad.server.serve --host 127.0.0.1 --port 8080 --seed-demo
```
Access the control plane at `http://127.0.0.1:8080/`.

---

## 3. Core CLI Workflows

### 3.1 Autonomous Orchestration
Execute a multi-agent parallel swarm against a task dependency graph:
```bash
python -m vad.orchestrator.cli \
  --manifest planning_artifacts/implementation_plan.md \
  --tracker planning_artifacts/implementation_tracker.md \
  --max-concurrency 4 \
  --heartbeat-timeout 300
```
- Automatically constructs execution DAGs.
- Verifies Definition of Ready (DoR) gates before dispatching.
- Monitors agent heartbeats and initiates automatic crash recovery.
- Maintains worktree-free staged commits.

### 3.2 Decision Routing
Evaluate intent and route to the most cost-effective and capable agent harness:
```python
from vad.decision.router import DecisionRouter
from vad.decision.catalog import DecisionCatalog

catalog = DecisionCatalog.load_default()
router = DecisionRouter(catalog)

decision = router.route(
    intent="Implement cross-harness DSSE attestation verification",
    context={"language": "python", "criticality": "high"}
)
print(f"Assigned Harness: {decision.selected_harness}")
print(f"Confidence: {decision.confidence:.2f}")
```

### 3.3 Multi-Harness Projection
Project universal VAD governance policies into native target harness files:
```bash
# Project to Claude Code configuration
python -m vad.harness.projector --target claude_code --out-dir ~/.claude

# Project to Cursor rules
python -m vad.harness.projector --target cursor --out-dir .

# Project to GitHub Copilot instructions
python -m vad.harness.projector --target copilot --out-dir .github
```

### 3.4 SkillGuard Integrity Verification
Verify that skills, slash commands, and hooks match cryptographic signatures:
```python
from vad.integrity.checker import IntegrityChecker

checker = IntegrityChecker.load_default()
report = checker.verify_workspace(".")
if not report.is_valid:
    print(f"Tampered files detected: {report.violations}")
```

---

## 4. Running the Verification Suite

To run all automated verification tests inside the clean WSL Docker container:
```bash
wsl docker run --rm -v /mnt/c/LocalProjects/condensate:/workspace -w /workspace -e PYTHONPATH=/workspace vad-runner:3.11 pytest tests/ -q
```

To run the security audit:
```bash
wsl docker run --rm -v /mnt/c/LocalProjects/condensate:/workspace -w /workspace -e PYTHONPATH=/workspace vad-runner:3.11 pytest tests/test_security_audit.py -v
```

---

## 5. Next Steps
- Read [Developer Workflow](user-journeys/developer-workflow.md) for pair programming with governed AI.
- Read [Autonomous Orchestrator Guide](how-to/autonomous-orchestrator.md) for scaling parallel swarms.
- Read [Operator TUI & Release Guardian](user-journeys/release-guardian-tui.md) for 2-person release gates.
- Read [Declarative Governance Guide](how-to/declarative-governance.md) to customize policies and gates.

