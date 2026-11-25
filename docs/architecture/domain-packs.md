# Scope B Domain Packs: Extending VAD Beyond Software Delivery

Verified Agentic Development (VAD) separates its **domain-neutral control plane** (Intent, Proof, Freshness, Risk × Evidence Autonomy Matrix, Approvals, and Evidence Ledger) from **domain-specific execution semantics** behind a typed adapter seam in [domain_pack.py](file:///c:/LocalProjects/condensate-arch-docs/verified-agentic-development/vad/adapters/domain_pack.py).

This seam enables non-coding autonomous agents—across infrastructure, operations, business workflows, and compliance—to inherit VAD's fail-closed governance without modifying VAD core packages (`vad/verify/`, `vad/policy/`, `vad/evidence/`, `vad/release/`).

---

## 1. Scope A vs. Scope B Positioning

| Dimension | Scope A: Verified Software Delivery Platform | Scope B: General Autonomous Agent Control System |
|---|---|---|
| **Primary Domains** | Software engineering, CI/CD, code & test verification, incident-driven patches (`DomainKind.SOFTWARE_DELIVERY`) | Infrastructure (`INFRASTRUCTURE`), Operations (`OPERATIONS`), Business (`BUSINESS`), Compliance (`COMPLIANCE`) |
| **Artifact Proposals** | Git diffs, patch journals, PR/MR proposals | Canonical `DomainArtifactProposal` payloads (Terraform plans, Kubernetes manifests, IAM/firewall changes, compliance attestations) |
| **Proof Execution** | Isolated test ladders (`pytest`, linters, contract checks) | Domain-native checks via `ProofExecutorAdapter` (`terraform plan`, policy-as-code checks, OPA/SOC2 control audits) |
| **Governance & Ledger Core** | `EIP`, `FreshnessValidator`, `AutonomyMatrixEvaluator`, `ReleaseDecisionEngine`, DSSE Evidence Ledger | **Identical VAD core** (`AutonomyMatrixEvaluator`, `ReleaseDecisionEngine`, `ReleaseDecision`, Evidence Ledger digests) |
| **Core Modifications Required** | N/A | **None** — domain packs register via `DomainPackRegistry` |

---

## 2. Three Adapter Protocols & Control-Plane Flow

Every Scope B domain pack implements three `@runtime_checkable` protocols and registers them in `DomainPackRegistry`:

```mermaid
flowchart LR
    Agent["Domain Agent / Host"] -->|"build_proposal()"| Proposal["DomainArtifactProposal (Canonical SHA-256)"]
    Proposal -->|"validate_proposal() + default_risk_tier() + required_assurance()"| Policy["DomainPolicyPack"]
    Proposal -->|"execute_obligation(verifier_actor != builder_actor)"| Verifier["ProofExecutorAdapter -> DomainProofOutcome"]
    Policy --> Eval["evaluate_domain_proposal()"]
    Verifier --> Eval
    Eval -->|"AutonomyEvaluationInput"| Matrix["AutonomyMatrixEvaluator + ReleaseDecisionEngine"]
    Matrix -->|"ReleaseDecision (digest-pinned)"| Ledger["Authoritative Evidence Ledger"]
    Matrix -->|"approved_decision_id (can_release=True)"| Apply["ArtifactProposalAdapter.apply_proposal()"]
```

### Protocol Summary

1. **`ArtifactProposalAdapter`**
   - Declares `domain: DomainKind`.
   - `build_proposal(...) -> DomainArtifactProposal`: Normalizes a domain change into a `DomainArtifactProposal` with an auto-computed canonical SHA-256 `payload_digest` and an explicit `rollback_plan`.
   - `apply_proposal(proposal, *, approved_decision_id) -> dict[str, Any]`: Executes the domain mutation only after `ReleaseDecisionEngine` emits an authorized `ReleaseDecision`.
2. **`ProofExecutorAdapter`**
   - Declares `domain: DomainKind`.
   - `execute_obligation(proposal, obligation_id, *, verifier_actor) -> DomainProofOutcome`: Runs an independent, isolated check for a proof obligation and returns a digest-pinned `DomainProofOutcome`.
3. **`DomainPolicyPack`**
   - Declares `domain: DomainKind`.
   - `default_risk_tier(proposal) -> RiskTier`: Maps domain semantics (e.g., production VPC peering vs. staging tag update) to VAD `RiskTier` (`low`, `medium`, `high`, `critical`).
   - `required_assurance(proposal) -> AssuranceLevel`: Declares the target `AssuranceLevel` (`A1_BASIC`–`A4_RESTRICTED`). Note that `AutonomyMatrixEvaluator` still enforces VAD's mandatory floor per `RiskTier`, so a domain pack cannot weaken core risk-tier assurance requirements.
   - `validate_proposal(proposal) -> list[str]`: Performs domain structural/safety checks (e.g., rejecting empty `rollback_plan` or forbidden wildcard principals).

---

## 3. Core Invariants Preserved Across Scope B

Routing a `DomainArtifactProposal` through `evaluate_domain_proposal(...)` guarantees every Scope B domain inherits VAD's core safety guarantees:

- **Builder/Verifier Separation:** `evaluate_domain_proposal` enforces `verifier_actor != builder_actor` before and after proof execution (`PermissionError` on self-verification) and forwards both actors to `AutonomyMatrixEvaluator` to block builder self-approval.
- **Evidence-Driven Autonomy Matrix:** `HIGH` proposals block with `ReleaseOutcome.HUMAN_APPROVAL_REQUIRED` until at least one distinct human guardian approves (`ReleaseOutcome.APPROVED_WITH_HUMAN`); `CRITICAL` proposals block with `ReleaseOutcome.RESTRICTED_AUTHORIZATION_REQUIRED` until an explicit pre-run `explicit_authorization_ref` and two distinct human guardians approve (`ReleaseOutcome.APPROVED_RESTRICTED`).
- **Evidence Ledger Traceability:** Both `proposal.payload_digest` (`eip_digest`) and the canonical digest over validation results + `DomainProofOutcome` records (`evidence_digest`) are bound onto the immutable `ReleaseDecision`.

---

## 4. Example: Infrastructure & Compliance Agents Without Core Edits

```python
from vad.adapters.domain_pack import (
    DomainKind,
    DomainPack,
    DomainPackRegistry,
    evaluate_domain_proposal,
)
from vad.release.decision import ReleaseOutcome

registry = DomainPackRegistry()
registry.register(
    DomainPack(
        domain=DomainKind.INFRASTRUCTURE,
        proposal_adapter=terraform_adapter,
        proof_executor=terraform_plan_verifier,
        policy_pack=infra_policy_pack,
    )
)

infra_pack = registry.get(DomainKind.INFRASTRUCTURE)
proposal = infra_pack.proposal_adapter.build_proposal(
    target_resource="aws_security_group.prod_ingress",
    summary="Restrict HTTPS ingress to corporate CIDR",
    payload={"environment": "production", "cidr_blocks": ["10.20.0.0/16"]},
    rollback_plan="terraform apply -var-file=previous.tfvars",
)

# 1. Blocked before human guardian sign-off
blocked = evaluate_domain_proposal(
    infra_pack,
    proposal,
    run_id="run-infra-001",
    builder_actor="infra-agent",
    verifier_actor="policy-verifier-bot",
)
assert blocked.can_release is False
assert blocked.outcome == ReleaseOutcome.HUMAN_APPROVAL_REQUIRED

# 2. Approved once human guardian signs off -> ready for apply_proposal()
approved = evaluate_domain_proposal(
    infra_pack,
    proposal,
    run_id="run-infra-002",
    builder_actor="infra-agent",
    verifier_actor="policy-verifier-bot",
    human_approvers=["sre-lead"],
)
assert approved.can_release is True
assert approved.outcome == ReleaseOutcome.APPROVED_WITH_HUMAN
```

---

## 5. Built-In Scope B Domain Packs (`vad/adapters/packs/`)

VAD ships four built-in Scope B domain packs, registered together by [`create_default_domain_pack_registry()`](file:///c:/LocalProjects/condensate-arch-docs/verified-agentic-development/vad/adapters/packs/__init__.py):

| Domain | Module & Factory | Adapter / Policy / Verifier Classes | Key Governance Rules |
|---|---|---|---|
| `DomainKind.INFRASTRUCTURE` | [infrastructure.py](file:///c:/LocalProjects/condensate-arch-docs/verified-agentic-development/vad/adapters/packs/infrastructure.py) (`create_infrastructure_domain_pack()`) | `InfrastructureProposalAdapter`, `InfrastructurePolicyPack`, `InfrastructureProofExecutor` | Terraform, Kubernetes, IAM, and network proposals. Requires non-empty `rollback_plan`; rejects wildcard CIDRs (`0.0.0.0/0`, `::/0`) unless `public_edge_approved=True`, wildcard IAM principals/actions (`*`), and privileged Kubernetes containers (`privileged: True`). Escalates destructive changes, IAM privilege escalation, or production `iam`/`kms`/`network_firewall`/`database_cluster` targets to `CRITICAL`. |
| `DomainKind.OPERATIONS` | [operations.py](file:///c:/LocalProjects/condensate-arch-docs/verified-agentic-development/vad/adapters/packs/operations.py) (`create_operations_domain_pack()`) | `OperationsProposalAdapter`, `OperationsPolicyPack`, `OperationsProofExecutor` | SLO remediation, traffic shifts, scaling, and runbook executions. Validates `rollback_plan`, `blast_radius_pct` (`0`–`100`), production `slo_target` + `health_check_probe`, and `preflight_healthy`. Escalates `blast_radius_pct > 50` or `region_failover`/`data_purge`/`break_glass` to `CRITICAL`. |
| `DomainKind.BUSINESS` | [business.py](file:///c:/LocalProjects/condensate-arch-docs/verified-agentic-development/vad/adapters/packs/business.py) (`create_business_domain_pack()`) | `BusinessProposalAdapter`, `BusinessPolicyPack`, `BusinessProofExecutor` | Pricing/discount rules, billing/credit adjustments, customer entitlements, and workflow transitions. Validates `rollback_plan`, non-negative `financial_impact_usd`, bounded `discount_pct` (`0`–`100`), and `policy_reference` when `financial_impact_usd >= 5000` or `affects_ledger_or_tax=True`. Escalates `>= 50000` or ledger/tax changes to `CRITICAL`. |
| `DomainKind.COMPLIANCE` | [compliance.py](file:///c:/LocalProjects/condensate-arch-docs/verified-agentic-development/vad/adapters/packs/compliance.py) (`create_compliance_domain_pack()`) | `ComplianceProposalAdapter`, `CompliancePolicyPack`, `ComplianceProofExecutor` | Compliance attestations, access reviews, and policy exceptions. Requires `rollback_plan` and `audit_ticket`; requires `expires_at` and `compensating_control` when `exception_request=True`. Escalates `SOC2-CC6`, `ISO27001-A.9`, `GDPR-ERASURE`, and `exception_request=True` to `CRITICAL`. |

---

## 6. End-to-End Execution Runner, Schema v13 Persistence & CLI/HTTP Surface

[`DomainPackExecutionRunner`](file:///c:/LocalProjects/condensate-arch-docs/verified-agentic-development/vad/workflows/domain_pack_execution.py) orchestrates Scope B proposals (`DomainExecutionRequest` -> `DomainExecutionResult`) through VAD's unmodified governance core:

1. **Proposal & Separation Checks:** Resolves the pack from `DomainPackRegistry` (defaulting to `create_default_domain_pack_registry()`), builds a canonical `DomainArtifactProposal`, and rejects empty or equal `builder_actor` / `verifier_actor` (`PermissionError`).
2. **Policy Validation & Isolated Proofs:** Runs `policy_pack.validate_proposal(proposal)` and executes each `obligation_id` via `proof_executor.execute_obligation(..., verifier_actor=verifier)` while re-checking `verifier_actor != builder_actor`.
3. **Autonomy Decision & Ed25519 DSSE Attestation:** Evaluates `evaluate_domain_proposal(...)` to emit an immutable `ReleaseDecision`, then signs a strict Ed25519 DSSE review attestation (`DSSEAttestor`) binding `proposal.payload_digest`, `release_decision.evidence_digest`, validation errors, and proof obligation outcomes.
4. **Guarded Apply & Schema v13 Persistence:** When `auto_apply_when_approved=True` and `release_decision.can_release` is `True`, executes `proposal_adapter.apply_proposal(proposal, approved_decision_id=release_decision.decision_id)`. Persists both `ReleaseDecision` and `StoredDomainProposalRecord` into SQLite Schema v13 (`domain_proposals` table in `ServerStore`).
5. **Advisory `ObservedFact` Publication:** Publishes a typed `ObservedFact` (`VERIFIED_DELIVERY`, `VERIFICATION_FAILURE`, `REJECTED_CHANGE`, or `POLICY_DENIAL`) tagged with `domain:<kind>` through `LearningLoopPublisher` and `MemoryGateway` (preserving memory non-authority).

### CLI & HTTP Usage

- **CLI:**
  ```bash
  vad domain-pack list --db .vad/control-plane/vad.sqlite3
  vad domain-pack evaluate \
    --db .vad/control-plane/vad.sqlite3 \
    --run-id run-infra-001 \
    --domain infrastructure \
    --target-resource aws_security_group.prod_ingress \
    --summary "Restrict HTTPS ingress to corporate CIDR" \
    --payload-json '{"environment":"production","cidr_blocks":["10.20.0.0/16"]}' \
    --rollback-plan "terraform apply -var-file=previous.tfvars" \
    --approver sre-lead
  ```
- **HTTP Endpoints:**
  - `GET /domain-packs`: Returns registered `domains` and persisted `proposals` from `domain_proposals`.
  - `POST /domain-packs/evaluate`: Executes a `DomainExecutionRequest` via `DomainPackExecutionRunner` (`200` on evaluation, `403` on actor-separation error, `422` on invalid input).
