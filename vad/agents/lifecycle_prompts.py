"""Lifecycle Prompt Libraries for Verified Agentic Development.

Defines rich system prompts, markdown artifact templates, and evaluation skeletons:
1. intent-steward: 11-section Executable Intent Package (EIP) template & prompt
2. adversarial-verifier: Critical / Warnings / Informational sections + invariant checklist format
3. post-mortem: Incident post-mortem with "VAD Cycle Feedback" (new invariants, proof obligations, skill updates, EIP triggers)
4. incident-responder: SEV-1 to SEV-4 triage and severity skeleton + containment ladder
5. observability-engineer: Metrics, logs, traces, SLO burn, and anomaly correlation findings format
6. local-integration-tester: Phase 3.5 report and guaranteed teardown lifecycle pattern
"""

from typing import Dict, List, Optional


# ============================================================================
# 1. Intent Steward: 11-Section EIP Template & Prompt
# ============================================================================

INTENT_STEWARD_PROMPT = """# Role: Intent Steward (EIP Architect)

You are the authoritative guardian of software intent in Verified Agentic Development.
Your responsibility is to formalize developer and business requirements into an Executable Intent Package (EIP) consisting of exactly 11 mandatory sections.

You never accept vague requests, unverified assumptions, or unbounded scope. Every goal must be coupled with measurable acceptance criteria, explicit non-goals, and formal invariants.
"""

EIP_11_SECTION_TEMPLATE = """# Executable Intent Package (EIP)

## 1. Metadata & Classification
- **EIP ID**: {eip_id}
- **Version**: {version}
- **Title**: {title}
- **Intent Author**: {author}
- **Status**: {status} <!-- draft | ready | in_progress | verified | deprecated -->
- **Risk Tier**: {risk_tier} <!-- low | moderate | high | regulated -->
- **Autonomy Tier**: {autonomy_tier} <!-- assisted | semi-autonomous | autonomous -->

## 2. Problem Statement & Business Context
{problem_statement}

## 3. Goals & Measurable Success Criteria
- [ ] **Goal 1**: {goal_1}
  - *Acceptance Criterion*: {criterion_1}
- [ ] **Goal 2**: {goal_2}
  - *Acceptance Criterion*: {criterion_2}

## 4. Explicit Non-Goals & Boundaries
- **Non-Goal 1**: {non_goal_1}
- **Boundary**: No modification of files outside `{scope_boundaries}`.

## 5. Invariants & Security Guarantees
- **INV-01 (Security)**: All writes must be credential-scanned; zero hardcoded secrets.
- **INV-02 (Separation of Duties)**: Builder agent cannot approve its own code diff or trigger remote push.
- **INV-03 (Functional)**: {invariant_functional}

## 6. Proof Obligations & Verification Matrix
| Obligation ID | Kind | Test Command / Target | Required | Status |
|---|---|---|---|---|
| PO-01 | unit | `pytest tests/test_unit.py` | Yes | Pending |
| PO-02 | property | `pytest tests/test_properties.py` | Yes | Pending |
| PO-03 | contract | `pytest tests/test_contracts.py` | Yes | Pending |
| PO-04 | security | `bash vad/integrity/ci_verify_registry.sh` | Yes | Pending |

## 7. Risk Tier & Threat Model
- **Identified Threats**: {threats}
- **Mitigation Protocols**: Policy-as-code gate enforcement, tool sandboxing, zero external network access.

## 8. Token Budget & MEES Target
- **Token Ceiling**: {max_tokens} tokens
- **Max Cost USD**: ${max_cost}
- **Max Loop Depth**: {max_loop_depth} iterations
- **MEES Target**: Minimum score >= 70 (No MR comment denial)

## 9. Rollout, Canary & Feature Flag Plan
- **Feature Flag Key**: `{feature_flag}`
- **Canary Strategy**: 5% traffic for 30 minutes with automated rollback on error rate > 0.1%.
- **Rollback Procedure**: Revert flag state; automated rollback receipt emitted.

## 10. Telemetry, Observability & SLI/SLO Hooks
- **SLI / Metrics**: Latency p99 < 150ms, error rate < 0.05%
- **Trace Spans**: `vad.execution.intent`, `vad.verification.run`
- **Audit Logging**: Structured JSON events emitted to audit trail.

## 11. Open Questions, Assumptions & Human Sign-Offs
- **Open Questions**:
{open_questions}
- **Intent Steward Sign-Off**: {sign_off_steward}
- **Verification Owner Sign-Off**: {sign_off_verifier}
"""


# ============================================================================
# 2. Adversarial Verifier: Critical/Warnings/Info + Invariant Checklist
# ============================================================================

ADVERSARIAL_VERIFIER_PROMPT = """# Role: Adversarial Verifier

You are an adversarial verifier whose sole objective is to invalidate proposed implementations, discover unhandled edge cases, falsify invariants, and detect separation-of-duties violations.

You do not write product code. You produce a structured verification report categorizing findings into Critical, Warnings, and Informational sections, accompanied by an Invariant Verification Checklist.
"""

ADVERSARIAL_VERIFIER_TEMPLATE = """# Adversarial Verification Report

## Verification Overview
- **Artifact Under Review**: `{artifact_id}`
- **Commit SHA**: `{commit_sha}`
- **Verifier Harness**: `{verifier_harness}`
- **Overall Determination**: {verdict} <!-- APPROVED | REJECTED -->

## 1. Critical Findings (Immediate Merge Blockers)
{critical_findings}
<!-- E.g.,
- [BLOCKER-01]: Secret leak detected in src/auth.py (AWS access key pattern).
- [BLOCKER-02]: Separation of duties bypass: builder attempted to transition task to staged.
-->

## 2. Warnings (Architectural Debt & Risk Factors)
{warning_findings}
<!-- E.g.,
- [WARN-01]: MEES score dropped to 58 due to 8-file spread across disparate subsystems.
- [WARN-02]: Missing property test for negative numbers in integer parser.
-->

## 3. Informational Findings (Non-Blocking Hygiene)
{info_findings}
<!-- E.g.,
- [INFO-01]: Docstring missing on helper function _sanitize_input.
-->

## 4. Invariant Verification Checklist
| Invariant ID | Statement | Verification Method | Status | Counterexample / Evidence |
|---|---|---|---|---|
| INV-01 | No secrets committed | Regex scan | {inv_01_status} | {inv_01_evidence} |
| INV-02 | Builder cannot self-approve | Policy engine | {inv_02_status} | {inv_02_evidence} |
| INV-03 | Pure function determinism | Hypothesis property | {inv_03_status} | {inv_03_evidence} |
| INV-04 | Backward-compatible schema | Contract validator | {inv_04_status} | {inv_04_evidence} |

## Cryptographic Attestation
- **DSSE Digest**: `{dsse_digest}`
- **Attested By**: `{attested_by}`
"""


# ============================================================================
# 3. Post-Mortem Template: VAD Cycle Feedback
# ============================================================================

POST_MORTEM_PROMPT = """# Role: Post-Mortem Facilitator

You conduct blameless engineering post-mortems following outages, test escapes, or policy bypasses.
Your primary objective is closing the control loop through the mandatory "VAD Cycle Feedback" section: converting incidents into machine-verifiable invariants, proof obligations, skill ledger entries, and EIP admission criteria.
"""

POST_MORTEM_TEMPLATE = """# Incident Post-Mortem

## Incident Overview
- **Incident Ref**: {incident_id}
- **Severity**: {severity} <!-- SEV-1 | SEV-2 | SEV-3 | SEV-4 -->
- **Start Time**: {start_time}
- **Resolution Time**: {resolved_time}
- **Time to Detect (TTD)**: {ttd}
- **Time to Mitigate (TTM)**: {ttm}

## 1. Executive Summary & Impact
{executive_summary}

## 2. Root Cause Analysis (5 Whys)
{root_cause_analysis}

## 3. Timeline of Events
{timeline}

## 4. VAD Cycle Feedback (Continuous Assurance Loop)
Every incident must permanently harden the engineering system through four control feedback outputs:

### 4.1 New Invariants
Invariants that will be added to future EIPs and policy-as-code:
- **INV-{new_inv_id}**: {new_invariant_statement}

### 4.2 New Proof Obligations
Executable checks and test suites required in CI:
- **PO-{new_po_id}**: {new_proof_obligation} (`{test_command}`)

### 4.3 Skill Updates & Caveats
Updates made to `skills/` and recorded in `SkillLedger`:
- **Skill**: `{affected_skill}`
- **Caveat Recorded**: {skill_caveat}
- **Guard Added**: {skill_guard_update}

### 4.4 EIP Triggers & Gate Hardening
Updates to Definition of Ready (DoR) or PreToolUse admission rules:
- **DoR Check Added**: {dor_update}
- **PreToolUse Hook Update**: {pre_tool_update}
"""


# ============================================================================
# 4. Incident Responder: Triage & Severity Skeleton
# ============================================================================

INCIDENT_RESPONDER_PROMPT = """# Role: Incident Responder

You are an incident responder orchestrating emergency containment, triage, and mitigation for automated systems.
You categorize severity strictly according to the VAD Severity Matrix (SEV-1 to SEV-4) and immediately execute containment protocols before debugging.
"""

INCIDENT_TRIAGE_SKELETON = """# Incident Triage & Response Plan

## 1. Severity Classification
- **Assigned Severity**: {severity}
  - `SEV-1 (Critical)`: System outage, data loss, secret compromise, or autonomous loop escape.
  - `SEV-2 (High)`: Core workflow degraded, verifier bypass vulnerability, or high blast radius.
  - `SEV-3 (Medium)`: Non-critical feature impaired, transient CI failure, or localized performance drift.
  - `SEV-4 (Low)`: Minor defect, non-impacting telemetry failure, or cosmetic UI anomaly.

## 2. Immediate Containment Checklist
- [ ] Emergency Circuit Breaker: Tripped globally (`vad circuit-breaker trip`).
- [ ] Revoke Active Leases: Force expired for all in-flight runner agents.
- [ ] Feature Flag Rollback: Flag `{feature_flag}` set to `false`.
- [ ] Lock Staging & Push Gates: No staged commits admitted to remote.

## 3. Roles & Incident Team
- **Incident Commander**: {commander}
- **Operations Lead**: {ops_lead}
- **Investigator / RCA Owner**: {investigator}
- **Communications**: {comms_lead}

## 4. Containment & Mitigation Log
{containment_log}
"""


# ============================================================================
# 5. Observability Engineer: Findings Format
# ============================================================================

OBSERVABILITY_ENGINEER_PROMPT = """# Role: Observability Engineer

You analyze telemetry signals across distributed traces, Prometheus metrics, structured audit logs, and OpenTelemetry events.
Your findings quantify latency bottlenecks, anomalous token consumption spikes, and error budget burn.
"""

OBSERVABILITY_FINDINGS_TEMPLATE = """# Observability Engineering Findings

## Telemetry Diagnostic Summary
- **Evaluation Window**: {window_start} to {window_end}
- **Target Subsystem / Service**: `{service_name}`
- **Total Invocations Analyzed**: {sample_count}

## 1. Metric Anomalies & Baseline Drift
- **p95 Latency**: {p95_latency}ms (Baseline: {p95_baseline}ms, Drift: {latency_drift}%)
- **Error Rate**: {error_rate}% (Threshold: {error_threshold}%)
- **Token Burn Rate**: {tokens_per_sec} tokens/sec (Ceiling: {token_ceiling})

## 2. Log Patterns & Stack Trace Signatures
{log_signatures}

## 3. Distributed Trace Bottlenecks
{trace_bottlenecks}

## 4. SLO Budget Impact
- **Target SLO**: {slo_target}
- **Burn Rate**: {burn_rate}x
- **Remaining Error Budget**: {remaining_budget}%

## 5. Correlated Findings & Remediation Advice
{recommendations}
"""


# ============================================================================
# 6. Local Integration Tester: Phase 3.5 Report & Guaranteed Teardown
# ============================================================================

LOCAL_INTEGRATION_TESTER_PROMPT = """# Role: Local Integration Tester

You execute Phase 3.5 local multi-service integration verification in throwaway sandbox environments.
You strictly enforce guaranteed teardown: every container, mock server, temporary socket, and volume must be pruned in a `finally` block even if the test suite panics.
"""

PHASE_3_5_REPORT_TEMPLATE = """# Phase 3.5 Local Integration Test Report

## Sandbox Environment
- **Sandbox Root**: `{sandbox_dir}`
- **Isolation Mode**: Ephemeral Container / Local Virtual Subnet
- **Orchestration Tool**: Docker Compose / In-Process Runner
- **Start Time**: {start_time}

## 1. Setup & Pre-Flight Validation
- [x] Host ports reserved and checked for conflicts
- [x] Database test fixture seeded from migration baseline
- [x] Mock OAuth & Remote Jira connectors initialized

## 2. Execution Matrix
| Obligation | Target Endpoint / Module | Protocol | Result | Latency |
|---|---|---|---|---|
| PO-INT-01 | `/api/v1/work-items` | HTTP JSON | {res_01} | {lat_01}ms |
| PO-INT-02 | `/mcp` tool dispatch | JSON-RPC | {res_02} | {lat_02}ms |
| PO-INT-03 | SQLite Concurrent Leases | Multi-Thread | {res_03} | {lat_03}ms |

## 3. Phase 3.5 Verification Verdict
- **Status**: {integration_status} <!-- PASS | FAIL -->
- **Assertions Passed**: {passed_count}/{total_count}

## 4. Guaranteed Teardown Guarantee
- **Teardown Block Executed**: {teardown_executed}
- **Containers Stopped & Removed**: {containers_pruned}
- **Volumes Removed**: {volumes_removed}
- **Leaked Resource Audit**: Zero lingering sockets or file descriptors.
"""
