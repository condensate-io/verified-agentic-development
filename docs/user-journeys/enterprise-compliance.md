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
