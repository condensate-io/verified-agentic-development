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
