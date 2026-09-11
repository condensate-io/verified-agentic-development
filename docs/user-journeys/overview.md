# VAD Enterprise — User Journeys & Persona Overview

Verified Agentic Development (VAD) Enterprise is engineered to support a spectrum of participants across the software development lifecycle — from human software engineers using AI coding assistants to fully autonomous agent swarms, operator release guardians, and enterprise compliance auditors.

---

## 1. Persona Matrix

| Persona | Primary Interface | Core Responsibilities | Key VAD Modules |
|---------|-------------------|----------------------|-----------------|
| **Software Engineer** | Claude Code / Cursor / Copilot | Authors code with governed AI assistance; writes tests; validates proof obligations. | Claude Code Plugin, Multi-Harness Projector, Scope Resolver, Pre/Post Tool Hooks |
| **Autonomous Swarm** | CLI Orchestrator / Subprocess Pool | Executes large multi-task plans in parallel without worktree contention; recovers automatically from crashes. | Autonomous Orchestrator, Dep-Graph DAG, Admission Control, Heartbeat Monitor |
| **Platform Operator / Release Guardian** | Textual TUI / Web Control Plane | Monitors live routing decisions, calibration, agent health, and enforces the 2-person release approval rule. | Operator TUI, Decision Engine, Confirmation Gate, Web Dashboard |
| **Enterprise Auditor / CISO** | REST API / SIEM Exports | Verifies cryptographic workspace integrity, audits immutable event streams, generates SOC2/ISO reports. | SkillGuard Integrity Registry, Centralized Audit Hooks, SIEM Exporters, Compliance Reports |

---

## 2. End-to-End Enterprise Development Lifecycle

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

## 3. Navigation to Detailed Journeys

1. [Developer & AI Pair Programming Journey](developer-workflow.md)
2. [Autonomous Swarm Orchestration Journey](autonomous-orchestration.md)
3. [Operator Monitoring & Release Guardian in TUI](release-guardian-tui.md)
4. [Enterprise Compliance, Zero-Trust SkillGuard & SIEM Auditing](enterprise-compliance.md)
