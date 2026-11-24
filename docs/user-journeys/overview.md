# VAD Enterprise — User Journeys & Persona Overview

Verified Agentic Development (VAD) Enterprise is engineered to support a spectrum of participants across the software development lifecycle — from human software engineers using AI coding assistants to fully autonomous agent swarms, operator release guardians, and enterprise compliance auditors.

---

## 1. Persona Matrix

| Persona | Primary Interface | Core Responsibilities | Key VAD Modules |
|---------|-------------------|----------------------|-----------------|
| **Software Engineer** | Claude Code / Cursor / Copilot | Authors code with governed AI assistance; writes tests; validates proof obligations. | Claude Code Plugin, Multi-Harness Projector, Scope Resolver, Pre/Post Tool Hooks |
| **Autonomous Swarm** | CLI Orchestrator / Subprocess Pool | Executes large multi-task plans in parallel without worktree contention; recovers automatically from crashes. | Autonomous Orchestrator, Dep-Graph DAG, Admission Control, Heartbeat Monitor, Fenced Leases, Durable Checkpoints |
| **Platform Operator / Release Guardian** | Textual TUI / Web Control Plane / CLI | Monitors routing and agent health, approves High and Critical changes, and reviews candidate lessons. | Operator TUI, Decision Engine, Autonomy Matrix, Release Decisions, Lesson Review |
| **Enterprise Auditor / CISO** | REST API / CLI / SIEM Exports | Independently verifies runs from the ledger, audits event streams, reviews outcome metrics, generates SOC2/ISO reports. | SkillGuard Integrity Registry, Audit Trail Verifier, Outcome Metrics, SIEM Exporters, Compliance Reports |
| **Platform Engineer** | Config / CLI | Chooses the memory provider and agent hosts; keeps VAD local-first. | Memory Provider Interface, Host Session Tokens, Conformance Suite |

---

## 2. Authority Scales with Risk and Proof

VAD does not apply one automation level to every task. The authority granted depends on the risk of the change and the proof available.

| Risk | Example | What VAD allows |
|------|---------|-----------------|
| **Low** | Documentation or isolated test update | Autonomous release after checks |
| **Medium** | Internal API change | Independent verification required |
| **High** | Authentication or data migration | Human approval required |
| **Critical** | Production access or policy change | Restricted execution with explicit authorization |

Success is measured, not assumed: autonomous safe pass-through rate, escaped defects, verification false positives, recovery success, human review time, and cost per accepted change (`vad metrics autonomy`).

VAD governs agent hosts (Codex, Claude Code, Cursor, custom agents) rather than competing with them, and it does not depend on any one model vendor or memory system.

---

## 3. End-to-End Enterprise Development Lifecycle

```
[ Engineering Ask / Jira / Spec ]
               │
               ▼
[ 1. Decision Engine: DoR Gate & Harness Routing ]
               │
               ├── (Single Task) ──────────────► [ 2A. Developer Pair Programming ]
               │                                      (Claude Code / Cursor / Hooks)
               │                                                    │
               └── (Multi-Task Plan) ──────────► [ 2B. Autonomous Swarm Orchestrator ]
                                                      (DAG Tranches / Heartbeat Monitor)
                                                                    │
                                                                    ▼
                                                 [ 3. Cross-Harness Review & DSSE ]
                                                      (Separation of Duties / in-toto)
                                                                    │
                                                                    ▼
                                                 [ 4. Operator TUI & Release Guardian ]
                                                      (2-Person Approval / Rebase Guard)
                                                                    │
                                                                    ▼
                                                 [ 5. Production Promotion & Audit ]
                                                      (SOC2 Reports / SIEM Telemetry)
```

---

## 4. Navigation to Detailed Journeys

1. [Developer & AI Pair Programming Journey](developer-workflow.md)
2. [Autonomous Swarm Orchestration Journey](autonomous-orchestration.md)
3. [Operator Monitoring & Release Guardian in TUI](release-guardian-tui.md)
4. [Enterprise Compliance, Zero-Trust SkillGuard & SIEM Auditing](enterprise-compliance.md)
5. [Verified Delivery Flow (Evidence-Driven Autonomy)](verified-delivery.md)
6. [Learning Loop and Pluggable Memory](learning-loop-and-memory.md)
7. [Work Intake Connectors & Governed AI Execution](work-intake-connectors.md)
