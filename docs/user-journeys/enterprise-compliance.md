# User Journey: Enterprise Compliance, Zero-Trust SkillGuard & SIEM Auditing

This journey illustrates how a Chief Information Security Officer (CISO), compliance auditor, or platform security engineer uses VAD Enterprise to ensure regulatory compliance, maintain audit trails, and export telemetry to enterprise SIEM platforms.

---

## 1. Zero-Trust Workspace Verification with SkillGuard

Enterprise security policies mandate that developer workstations never run unapproved skills, altered instructions, or malicious prompt injections.

1. **Registry Verification:** `SkillGuard` maintains a signed catalog of permitted skills:
   ```python
   from vad.integrity.registry import IntegrityRegistry
   
   registry = IntegrityRegistry.load("schemas/integrity_registry.json")
   ```
2. **Tamper Detection:** If a developer or third-party package modifies a rule file:
   ```bash
   vad integrity check
   ```
   `SkillGuard` detects hash mismatches, blocks the session start, and notifies security operations.

---

## 2. Immutable Local & Centralized Audit Telemetry

Every tool invocation, decision route, policy gate evaluation, and agent transition is recorded in an append-only audit ledger:
- **Local Ledger:** `vad/audit/local_audit.py` writes structured JSONL events with cryptographic chaining.
- **Centralized Streaming:** `vad/audit/centralized_hooks.py` streams events over mutual TLS to the corporate audit intake server.

---

## 3. Real-Time SIEM Integration

VAD Enterprise includes built-in exporters for enterprise SIEM platforms:
- **Splunk:** HTTP Event Collector (HEC) JSON payload formatting.
- **Datadog:** Logs and metrics pipeline exporter.
- **Elasticsearch:** Standard Elastic Common Schema (ECS) mapping.

```python
from vad.audit.siem_export import SIEMExporter

exporter = SIEMExporter(target="datadog", endpoint="http://127.0.0.1:8088/v1/input")
exporter.export_events(events)
```

---

## 4. Automated Compliance Package Generation

For SOC2 Type II, ISO 27001, and FedRAMP audits, VAD compiles complete compliance packages:
```python
from vad.compliance.reports import ComplianceReportGenerator

generator = ComplianceReportGenerator()
report = generator.generate(
    framework="SOC2_Type_II",
    run_history_days=30,
    evidence_root="evidence/"
)

report.export_pdf("compliance-soc2-audit-package.pdf")
```
### Included Evidence Artifacts:
- **Access Control & RBAC Evidence (SSO / SAML 2.0 Logs)**
- **Separation of Duties Evidence (2-Person Rule Logs)**
- **Cryptographic Attestation Verification (DSSE Envelopes)**
- **Change Management Evidence (DoR Gates & Quality Gate Logs)**

---

## 5. Independent Audit of Any Run

An auditor can verify a run from VAD's own ledger, without trusting the builder agent or any memory provider:

```bash
vad audit verify-trail <run_id> --db .vad/control-plane/vad.sqlite3 --key-dir .vad/keys
```

The same check is available over HTTP as `GET /audit/verify-trail/{run_id}` (200 when verified, 422 when not). The command exits with a non-zero status if any check fails.

| Check | What it proves |
|-------|----------------|
| Evidence integrity | The stored evidence still matches its recorded digest (detects tampering). |
| EIP and proof plan digests | The intent and proof plan the run used are on record. |
| Actor separation | Builder and verifier were different (except low-risk, basic-assurance runs). |
| Verification passed | The authoritative verification result is a pass. |
| DSSE attestation | The Ed25519 signature is valid, approves the change, and matches the expected commit. |
| Release decision | The decision matches the evidence and no builder approved their own work. |
| Checkpoint chain | Stages and fence tokens only move forward. |
| Memory authority | No memory provider injected an approval, override, or bypass. |

Signing uses a local Ed25519 key generated under `.vad/keys/`. Strict mode refuses the old shared default secret.

---

## 6. Outcome Metrics for Governance Reviews

Autonomy is only useful if it is safe. VAD reports six measures:

```bash
vad metrics autonomy --db .vad/control-plane/vad.sqlite3
```

- **Autonomous safe pass-through rate:** changes released without manual intervention and without a later escaped defect.
- **Escaped defects:** incidents linked back to previously accepted runs.
- **Verification false positives:** rejections later judged to be wrong.
- **Recovery success rate:** interrupted runs that completed after recovery.
- **Human review time:** median and 95th percentile.
- **Cost per accepted change.**

The same data is served at `GET /metrics/autonomy`. Figures are broken down by risk tier.

---

## 7. Governance of Learned Rules

Lessons proposed by the system are advisory until a human approves them. Approval decisions are stored in VAD's ledger (`lesson_promotions`) with the reviewer's identity, and only `release_guardian` or `operator` roles can decide. Memory providers can be replaced or taken offline without changing any verification result, approval, or ledger entry. See [Learning Loop and Memory](learning-loop-and-memory.md).

---

## 8. Scope B Non-Coding Domain Pack Governance (`ComplianceDomainPack`, `InfrastructureDomainPack`, `OperationsDomainPack`, `BusinessDomainPack`)

Enterprise governance extends beyond code diffs to infrastructure, operational runbooks, business automation, and compliance controls through four built-in Scope B domain packs registered by `create_default_domain_pack_registry()` (see [Scope B Domain Packs](../architecture/domain-packs.md)):

- **Compliance (`create_compliance_domain_pack` / `CompliancePolicyPack`):** Enforces `rollback_plan` and `audit_ticket` on attestations/access reviews, requires bounded `expires_at` + `compensating_control` for policy exceptions (`exception_request=True`), and escalates critical control families (`SOC2-CC6`, `ISO27001-A.9`, `GDPR-ERASURE`) and exception requests to `RiskTier.CRITICAL`.
- **Infrastructure (`create_infrastructure_domain_pack` / `InfrastructurePolicyPack`):** Governs Terraform, Kubernetes, IAM, and network proposals; blocks wildcard CIDRs (`0.0.0.0/0`, `::/0` unless `public_edge_approved=True`), wildcard IAM principals/actions (`*`), and privileged Kubernetes containers (`privileged: True`).
- **Operations (`create_operations_domain_pack` / `OperationsPolicyPack`):** Governs SLO remediation, traffic shifts, and runbooks; enforces bounded `blast_radius_pct` (`0`–`100`) and production `slo_target` + `health_check_probe` requirements.
- **Business (`create_business_domain_pack` / `BusinessPolicyPack`):** Governs pricing/discount, billing, and entitlement changes; enforces `policy_reference` on high-impact (`>= $5000`) or ledger/tax changes and escalates `>= $50000` or ledger/tax impacts to `RiskTier.CRITICAL`.

All four packs execute through [`DomainPackExecutionRunner`](../../vad/workflows/domain_pack_execution.py), preserving `verifier_actor != builder_actor`, Risk × Evidence guardian approvals, strict Ed25519 DSSE attestations, Schema v13 `domain_proposals` persistence, and advisory `ObservedFact` publication:

```bash
vad domain-pack list --db .vad/control-plane/vad.sqlite3
vad domain-pack evaluate \
  --db .vad/control-plane/vad.sqlite3 \
  --run-id run-comp-001 \
  --domain compliance \
  --target-resource soc2-cc6-access-review \
  --summary "Quarterly privileged access attestation" \
  --payload-json '{"framework":"SOC2","control_family":"SOC2-CC6","audit_ticket":"AUD-402"}' \
  --rollback-plan "Revoke attestation record and reopen access review" \
  --approver ciso-guardian \
  --approver secops-lead \
  --auth-ref AUTH-SOC2-Q4
```

Equivalent HTTP endpoints: `GET /domain-packs` and `POST /domain-packs/evaluate`.

---

## 9. Stage 04 Continuous Verified Operations (`ContinuousVerifiedOperationsWorkflow` / `vad operations remediate`)

When runtime drift, SLO breaches, or security alerts occur in production, [`ContinuousVerifiedOperationsWorkflow`](../../vad/workflows/continuous_operations.py) closes the operational loop without bypassing VAD release gates:

1. **Incident Triage & Post-Mortem:** `IncidentResponder` triages the trigger into an `IncidentReport` and `PostMortemGenerator` generates a structured `PostMortem`.
2. **Escaped-Defect Linkage & Remediation EIP Seeding:** When `originating_run_id` is supplied, a `DefectLinkRecord` links the incident back to the originating run's evidence digest for escaped-defect tracking. `EIPSeeder` derives a bounded remediation `EIP` (escalating to `RiskTier.CRITICAL` for `SEV-1` or `--critical`), plus an incident `ObservedFact` and a candidate `PROPOSED` `InferredLesson`.
3. **Governed Remediation Delivery:** Executes `VerifiedDeliveryWorkflow` under `PolicyStage.RUNTIME`, enforcing independent verification, guardian approvals, and DSSE signing before remediation release:

```bash
vad operations remediate \
  --db .vad/control-plane/vad.sqlite3 \
  --run-id rem-drift-001 \
  --trigger-type runtime_drift \
  --description "Production ingress rule drifted from approved CIDR policy" \
  --originating-run-id run-infra-001 \
  --approver sre-lead
```

Equivalent HTTP endpoint: `POST /operations/remediate`.

---

## 10. Multi-EIP Objective Audit Verification (`vad audit verify-objective`)

For Stage 03 programmes decomposed into multiple child `EIP`s under an `EngineeringObjective`, auditors can verify the entire hierarchy and every leaf run trail in one command via [`verify_objective_audit_trail`](../../vad/audit/verify_trail.py):

```bash
vad audit verify-objective eo-gateway-mtls \
  --db .vad/control-plane/vad.sqlite3 \
  --run-id-prefix obj-run \
  --key-dir .vad/keys
```

The same verification report (`ObjectiveAuditTrailReport`) is served at `GET /audit/verify-objective/{objective_id}` (`200` when verified, `422` otherwise) and checks:

| Check | What it proves |
|---|---|
| `objective_loaded` | The target node exists in `intent_hierarchy_nodes` as an `EngineeringObjective`. |
| `hierarchy_chain_floors` | All 3-tier parent links (`StrategicObjective -> Programme -> EngineeringObjective`), `risk_floor`, `assurance_floor`, `inherited_invariants`, DAG acyclicity, and `max_total_cost_usd` ceilings hold. |
| `decomposition_approved` | `decomposition_status` is `approved`, `executing`, or `verified` with a non-empty `approved_by` identity. |
| `objective_rollup` | Every child `EIP` is verified with fresh evidence and `cross_eip_proof_passed` is `True`. |
| `child_run_trails` | Every child `EIP` run passes full `verify_run_audit_trail` checks and matches its recorded `evidence_digest`. |
