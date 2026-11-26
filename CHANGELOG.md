# Changelog

All notable changes to Verified Agentic Development (VAD) are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- Front-door documentation aligned to Stage 5 (README, license SPDX `BUSL-1.1`, public site copy).

## [0.5.0] - 2026-11-24

### Added — Stage 5: Evidence-Driven Autonomy & Memory-Agnostic Control Plane

- **Risk × Assurance autonomy matrix** (`vad/policy/autonomy_matrix.py`) with risk tiers `low` / `medium` / `high` / `critical` and assurance levels `A1_BASIC` → `A4_RESTRICTED`.
- **Release decision engine** (`vad/release/decision.py`) producing immutable, replayable `ReleaseDecision` outcomes bound to EIP and evidence digests.
- **Verified Delivery Flow** (`vad/workflows/verified_delivery.py`) — 8-step closed loop from EIP creation through independent verification, policy, release, memory sync, and lesson promotion.
- **Fenced leases** — monotonic `fence_token` on task leases; stale/zombie writers receive `409 Conflict`.
- **Idempotency records** and durable **RunCheckpoint** stages across the verified delivery lifecycle.
- **Schema v12** SQLite extensions: fenced leases, idempotency ledger, checkpoints, release decisions, lesson promotions, delivery outcomes, defect links.
- **Pluggable memory providers** (`null`, `local`, `condensate_core`, `mem0`, `graphiti`, custom HTTP/MCP) behind `MemoryProviderProtocol` / `MemoryGateway`.
- **`MemoryAuthorityGuard`** — memory remains advisory-only; providers cannot mutate evidence, verification, approvals, or policy.
- **Human lesson promotion gate** — observed facts vs candidate inferred lessons; only approved lessons enrich proofs/policy.
- **Ed25519 DSSE attestation** via local key provider (`vad/signing/ed25519_local.py`); strict mode rejects legacy hardcoded HMAC secrets.
- **Independent verification** — isolated executor, freshness validator, tree/env digests, builder ≠ verifier for A2+.
- **OPA Rego policy pack** (`policies/vad.rego`) for optional autonomy cross-checks.
- **Hierarchical Intent & Programme Governance** (`vad hierarchy`, `vad audit verify-objective`) — Strategic Objective → Programme → Engineering Objective → EIP with non-downgradable Risk/Assurance floors, cost/blast-radius ceilings, topological parallel tranches, and coordinated rollback.
- **Four Non-Coding Domain Packs** (`vad domain-pack`) — Infrastructure (Terraform/K8s/IAM), Operations (runbooks/traffic shifts), Business (pricing/entitlement/billing), and Compliance (SOC2/ISO27001/GDPR).
- **Continuous Verified Operations** (`vad operations remediate`) — closed-loop incident triage, post-mortem generation, escaped-defect linkage, `HIGH`/`CRITICAL` remediation EIP seeding, and human-gated lesson extraction.
- **Opt-In Remote Workers & Enterprise RBAC** (`vad remote-workers`) — mTLS/OIDC worker identities, SHA-256 artifact transfer manifests, and release-guardian group co-authorization.
- **Domain packs**, remote worker contracts, host role isolation / session tokens.
- **CLI**
  - `vad workflow verified-delivery`
  - `vad lessons list|approve|reject`
  - `vad metrics autonomy`
  - `vad audit verify-trail`
- **Docs**: verified delivery flow, memory providers, authority boundaries, domain packs, remote workers, hierarchical intent RFC, Stage 5 user journeys.

### Changed

- EIP schema / contracts extended for critical risk, assurance levels, and freshness policies.
- Condensate Core positioned as the reference memory provider among interchangeable backends.

## [0.1.0] - 2026-11

### Added

- Level 4 local control plane, work intake connectors (GitHub / GitLab / Jira / generic), governed MCP surface.
- Autonomous DAG orchestrator, multi-harness runners, Operator TUI, SkillGuard, SIEM audit ledger.
- Two-person release guardian gates, DSSE attestation foundation, declarative governance (MEES).
- Open-Core positioning with Condensate Core / TurboQuant Qdrant; relicensed to Business Source License 1.1.

[Unreleased]: https://github.com/condensate-io/verified-agentic-development/compare/v0.5.0...HEAD
[0.5.0]: https://github.com/condensate-io/verified-agentic-development/releases/tag/v0.5.0
[0.1.0]: https://github.com/condensate-io/verified-agentic-development/releases/tag/v0.1.0
