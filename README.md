# Verified Agentic Development (VAD): Enterprise Control-Plane & Governance Platform

[![License: BSL 1.1](https://img.shields.io/badge/License-BSL_1.1-red.svg)](LICENSE)
[![Commercial: Request License](https://img.shields.io/badge/Commercial-Requires_License-orange.svg)](mailto:support@condensate.io)
[![Python: 3.11+](https://img.shields.io/badge/Python-3.11%2B-brightgreen.svg)](pyproject.toml)
[![Docker: vad-runner](https://img.shields.io/badge/Verification-Docker_Isolated-orange.svg)](Dockerfile)
[![Level: Enterprise L4](https://img.shields.io/badge/Maturity-Level_4_Autonomous_Governed-purple.svg)](maturity_model.md)

> **Free for local evaluation & non-commercial use (BSL 1.1).** Deploying VAD across a commercial engineering team? **Request a pilot / commercial license at [support@condensate.io](mailto:support@condensate.io).**

> **Verified Agentic Development (VAD)** is an enterprise control-system model and governance platform for AI-assisted and autonomous software engineering. VAD transforms software delivery from unverified, ad-hoc code generation into a closed-loop control system: **Intent is formalized, proof obligations are machine-executable, agents operate within bounded sandbox loops, and releases require cryptographic attestation.**

### Open-Core Ecosystem & Licensing

VAD is part of the **Condensate Open-Core** stack:
- **Condensate Core (`condensate-io/core`)** & **TurboQuant Qdrant (`condensate-io/qdrant`)**: Permissive **Apache 2.0** memory engine, contradiction resolution, cryptographic provenance, MCP server, and SIMD retrieval.
- **Verified Agentic Development (this repository)**: **BSL 1.1** enterprise control plane & governance layer (two-person release guardian gates, cross-harness review, SIEM audit ledger, and multi-client orchestration).

### 10-Line Quickstart (Zero-Config Local SQLite + DSSE Attestation)

```python
from vad.control_plane.sdk import LocalControlPlaneClient
from vad.control_plane.events import ControlPlaneEvent, ControlPlaneEventKind, ControlPlaneEventStatus
from vad.review.dsse_attestation import DSSEAttestor

cp = LocalControlPlaneClient.from_db_path(".vad/control-plane/vad.sqlite3")
attestor = DSSEAttestor()
env = attestor.create_review_attestation("abc1234", "approved", "verifier-1", {"tests": 12})
assert attestor.verify_review_attestation(env, "abc1234")
cp.emit_event(ControlPlaneEvent(sequence=1, client_id="cli-1", run_id="run-1", kind=ControlPlaneEventKind.PROOF_FINISHED, status=ControlPlaneEventStatus.PASSED, actor="verifier-1", role="verifier", summary="DSSE attested"))
print("Audit events:", len(cp.store.list_control_plane_events(run_id="run-1")))
```

---

## 1. Current Implementation Status

VAD has evolved from an initial concept into a full **Level 4 Enterprise Governed Platform** with automated work intake and agent orchestration.

### Core Capabilities

- **Universal Work Intake Connectors (`vad/connectors/`):**
  - Bidirectional integrations with **GitHub Issues & PRs** (HMAC-SHA256, Checks API), **GitLab Issues & MRs** (token auth, MR discussions), **Atlassian Jira** (Cloud & Server, ADF-to-Markdown sanitization, workflow transitions), and **Generic Webhooks** (Linear, Asana, Monday, CI/CD).
  - Ingress Webhook Receiver with cryptographic signature validation, delivery UUID replay guard, and deduplication.
- **Governed MCP Surface for AI Agents (`vad/adapters/`, `vad/control_plane/`):**
  - Standardized Model Context Protocol (MCP) server: `vad mcp work-intake` with automated one-command installation for **Claude Code** and **Cursor**.
  - Governed agent tool surface: `work_intake_discover`, `work_intake_claim` (task leases), `work_intake_report_progress`, `work_intake_submit_proof` (evidence bundles), and `work_intake_escalate`.
- **Inbound & Outbound Governance Gates (`vad/policy/`):**
  - **Definition of Ready (DoR) Gate:** Automated ingress evaluation checking prompt injection safety, title/description completeness, and acceptance criteria.
  - **Two-Person Release Guardian Gate:** Strictly enforces that closing a remote ticket or merging a PR requires an SHA-256 evidence bundle digest and **two distinct actors** (builder agent + human release guardian).
  - **Bidirectional Sync Worker:** Background queue worker handling outbound status transitions, comments, and attestations with retry backoff and governance hold protection.
- **Autonomous Orchestrator (`vad/orchestrator/`):**
  - Directed Acyclic Graph (DAG) task scheduling, dependency resolution, admission control, parallel agent dispatch, heartbeat monitoring, and staged commit approval.
- **Multi-Harness Agent Runners (`vad/runners/`):**
  - Native execution adapters for Claude Code, Cursor, GitHub Copilot, Codex, Aider, and Python SDK.
- **Cross-Harness Review & DSSE Attestation (`vad/review/`, `vad/signing/`):**
  - Cryptographic DSSE signatures, independent verifier attestation, and rebase stability guards.
- **Declarative Governance (`vad/policy/`):**
  - Configurable Quality Gates (MEES effort score, cyclomatic complexity, line deltas, dependency manifest checks), maturity progression gate (L1–L5), and scope boundary enforcement.
- **Enterprise Audit & Telemetry (`vad/audit/`):**
  - Append-only SQLite audit ledger, centralized streaming hooks, SIEM export, and token/cost budgeting.
- **Operator Terminal UI (`vad/tui/`):**
  - Interactive Textual TUI dashboard for real-time decision inspection, dependency graph navigation, live telemetry, and staged release gates.
- **SkillGuard Zero-Trust Registry (`vad/integrity/`):**
  - Cryptographic SHA-256 hash verification across all custom skills, prompt templates, and lifecycle hooks.
- **Semantic Memory & Learning Ledger (`vad/memory/`):**
  - Persistent skill ledger, learning gaps miner, and optional native integration with [Condensate Core](https://github.com/condensate-io/core).

---

## 2. Architecture

```
External Ticketing & CI/CD       VAD Control Plane & Ingress Layer        AI Agent MCP Surface
──────────────────────────       ─────────────────────────────────        ────────────────────
┌────────────────────────┐       ┌───────────────────────────────┐        ┌──────────────────┐
│  GitHub Issues & PRs   │──────▶│ Webhook Ingress & Auth Gate   │        │   Claude Code    │
│  (HMAC-SHA256, Checks) │◀──────│ (HMAC, Replay Guard, Ingress) │        │   Claude Desktop │
└────────────────────────┘       └──────────────┬────────────────┘        │   Cursor Agent   │
┌────────────────────────┐                      │                         └────────┬─────────┘
│  GitLab Issues & MRs   │──────────────────────┤                                  │ (JSON-RPC 2.0 /
│  (Token Auth, Notes)   │◀─────────────────────┤                                  │  stdio / SSE)
└────────────────────────┘                      ▼                                  ▼
┌────────────────────────┐       ┌───────────────────────────────┐        ┌──────────────────┐
│  Atlassian Jira        │──────▶│ Universal Work Intake Router  │◀──────▶│ Governed MCP     │
│  (Cloud/Server, ADF)   │◀──────│ • Definition of Ready (DoR)   │ Bi-dir │ Gateway Surface  │
└────────────────────────┘       │ • Capability & Role Matching  │ Sync   │ • Lease Claim    │
┌────────────────────────┐       │ • Token Budget & Risk Router  │ Worker │ • Tool Auth      │
│  Generic Webhooks      │──────▶└──────────────┬────────────────┘        │ • Proof Submiss. │
│  (Linear, Asana, CI/CD)│                      ▼                         │ • 2-Person Gate  │
└────────────────────────┘       ┌───────────────────────────────┐        └──────────────────┘
                                 │ VAD Autonomous Orchestrator   │
                                 │ • DAG Dependency Engine       │
                                 │ • Disposable Docker Runners   │
                                 │ • Staged Operator Approval    │
                                 └──────────────┬────────────────┘
                                                ▼
                                 ┌───────────────────────────────┐
                                 │ Operator TUI & Dashboard      │
                                 │ (Real-time Decisions, Graph,  │
                                 │  Audit Ledger & Analytics)    │
                                 └───────────────────────────────┘
```

---

## 3. Quickstart Guide

### Prerequisites
- Python 3.11+
- Docker (or Docker Desktop via WSL 2 on Windows)
- Git

### Installation
```bash
# Clone the repository
git clone https://github.com/condensate-io/verified-agentic-development.git
cd verified-agentic-development

# Create and activate virtual environment
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Install in development mode
pip install -e .
```

### 1. Connecting Claude Code & Cursor to Governed Work Intake
```bash
# Configure Claude Code automatically
vad mcp work-intake --install claude

# Configure Cursor automatically
vad mcp work-intake --install cursor

# Run the MCP stdio server manually
vad mcp work-intake --db .vad/control-plane/vad.sqlite3
```

### 2. Running the Local Control Plane & Webhook Server
```bash
# Start the local Level 4 control plane server
vad control-plane serve --port 8080 --db .vad/control-plane/vad.sqlite3

# The webhook ingress endpoint is live at:
# POST http://localhost:8080/api/v1/webhooks/github
# POST http://localhost:8080/api/v1/webhooks/gitlab
# POST http://localhost:8080/api/v1/webhooks/jira
# POST http://localhost:8080/api/v1/webhooks/generic
```

### 3. Launching the Operator TUI
```bash
# Launch the real-time Operator terminal interface
vad tui
```

### 4. Running the Verification Suite in Docker
All verification must execute inside the isolated Docker runner:
```bash
# Build the runner container
docker build -t vad-runner:3.11 .

# Run the full test suite
docker run --rm -v $(pwd):/workspace -w /workspace -e PYTHONPATH=/workspace vad-runner:3.11 pytest -v
```

---

## 4. Foundational Principles

1. **Intent is Primary:** Every code modification originates with explicit, structured intent—formalized in an Executable Intent Package (EIP) containing goals, non-goals, invariants, constraints, and risk classification.
2. **Proof is Mandatory:** Nothing is complete until executable proof obligations pass (unit, property, fuzz, and security boundary tests).
3. **Separation of Duties:** Builder agents, verifier agents, and human release guardians remain strictly isolated. A single actor cannot approve its own work.
4. **Continuous Governance:** Cryptographic audit trails, DSSE signatures, and evidence bundles are generated continuously during development—never assembled retroactively.
5. **Closed-Loop Control:** Production telemetry, test outcomes, and incident post-mortems feed back into tightened invariants and updated proof plans.

---

## 5. The Control-System Model

VAD models software delivery as a closed-loop engineering control system:

| Control System Element | Software Engineering Equivalent |
|---|---|
| **Desired State** | Structured Intent (EIP) & Invariants |
| **Controller** | Bounded Verification Loop & Quality Gates |
| **Actuator** | Builder Agent (Claude Code, Cursor, Codex) |
| **Sensor** | Automated Tests & Disposable Docker Execution |
| **Disturbance** | Codebase drift, unexpected inputs, flaky dependencies |
| **Feedback** | Telemetry, incident post-mortems, and retrospective learning |

---

## 6. Documentation Suite

Comprehensive guides and operational manuals are available under [`docs/`](docs/):

### Guides & Manuals
- [Quickstart Guide](docs/quickstart.md) — 5-minute setup and guided walkthrough.
- [Autonomous Orchestrator Guide](docs/how-to/autonomous-orchestrator.md) — Multi-agent parallel task scheduling.
- [Work Intake & Governed MCP Guide](docs/how-to/work-intake-and-mcp.md) — Production connector and MCP setup.
- [Declarative Governance Guide](docs/how-to/declarative-governance.md) — Quality gates, policies, and invariants.
- [Cross-Harness Review & DSSE Attestation](docs/how-to/cross-harness-review.md) — Cryptographic signing and review.
- [Incident Response & Post-Mortem](docs/how-to/incident-response.md) — Automated incident resolution loops.
- [Claude Code Plugin Guide](docs/how-to/claude-code-plugin.md) — 7 lifecycle hooks and slash commands.
- [Condensate Core Integration Guide](docs/condensate-integration.md) — Semantic memory engine integration.

### User Journeys
- [Developer Pair Programming Journey](docs/user-journeys/developer-workflow.md) — Daily workflow with governed AI.
- [Work Intake & Remote Ticket Resolution](docs/user-journeys/work-intake-connectors.md) — Jira/GitHub Issue to verified release.
- [Autonomous Swarm Orchestration](docs/user-journeys/autonomous-orchestration.md) — Scaling parallel agents.
- [Release Guardian & 2-Person Release Gates](docs/user-journeys/release-guardian-tui.md) — Production release sign-off.
- [Enterprise Compliance & SIEM Audit](docs/user-journeys/enterprise-compliance.md) — Continuous compliance reporting, independent audit, and outcome metrics.
- [Verified Delivery Flow](docs/user-journeys/verified-delivery.md) — Risk-based autonomy from request to verified release.
- [Learning Loop & Pluggable Memory](docs/user-journeys/learning-loop-and-memory.md) — Facts, reviewed lessons, and memory providers.

---

## 7. Integration with Condensate Core (Apache 2.0)

While VAD provides the BSL 1.1 governance loop, execution control plane, and connector surface, [`condensate-io/core`](https://github.com/condensate-io/core) (**Apache 2.0**) serves as the permissive open-source causal memory engine and MCP server. VAD's `MemoryGateway` persists evidence bundles, evaluation outcomes, and retrospective learnings directly into Condensate Core.

See the [Condensate Core Integration Guide](docs/condensate-integration.md) for full details.

---

## 8. License & Open-Core Commercial Use

Verified Agentic Development (VAD) is licensed under the **Business Source License 1.1 (BSL 1.1)** (converting to Apache 2.0 on `2030-01-01`). See [`LICENSE`](LICENSE) for complete terms. For permissive open-source agent memory and MCP tooling without commercial restrictions, see **[Condensate Core (`Apache 2.0`)](https://github.com/condensate-io/core)**.

### Non-Commercial & Evaluation Use
You are free to use, modify, and inspect VAD for personal projects, academic research, evaluation, testing, and non-commercial open-source contributions free of charge, subject to the **Mandatory Attribution Condition**.

### Mandatory Attribution Notice
Any copy, fork, derivative work, or deployment of VAD must retain and prominently display the following notice in user interfaces, documentation, and CLI/API banners:

> **Powered by Condensate Verified Agentic Development (VAD)**  
> Copyright (c) 2024–2026 Condensate Systems Pty Ltd

### Commercial Licensing
**Any use of VAD for commercial purposes** (including but not limited to: internal production engineering pipelines within a business/enterprise, commercial SaaS hosting, fee-charging services, or embedding into proprietary commercial platforms) **is strictly prohibited without a separate Commercial License.**

To acquire a commercial license or discuss enterprise pilot deployments:
- 📧 **Email:** [support@condensate.io](mailto:support@condensate.io)
- 🌐 **Website:** [condensate.io](https://condensate.io)
