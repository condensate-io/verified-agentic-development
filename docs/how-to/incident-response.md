# How-To: Automate Incident Response, Containment, and Post-Mortems

When an autonomous run experiences a policy violation, unexpected crash, or test regression, VAD initiates automated incident response, generates a standardized post-mortem, and seeds corrective EIPs into the skill ledger.

---

## 1. Automated Incident Detection and Containment

The `IncidentResponder` halts the execution loop, isolates affected branches, and revokes task leases:

```python
from vad.incident.incident_responder import IncidentResponder

responder = IncidentResponder()
incident = responder.trigger_incident(
    run_id="run-902",
    reason="Policy violation: unauthorized network request outside local allowlist",
    severity="high"
)

print(f"Incident ID: {incident.incident_id}")
print(f"Run Quarantined: {incident.quarantined}")
```

---

## 2. Generating Standardized Post-Mortems

VAD generates standardized post-mortem reports conforming to `schemas/post_mortem.yaml`:

```python
from vad.incident.post_mortem import PostMortemGenerator

generator = PostMortemGenerator()
report = generator.generate_report(incident)

print(report.to_markdown())
```

### Post-Mortem Schema Elements
- **Incident Summary & Timeline**
- **Root Cause Analysis (5-Whys)**
- **Blast Radius & Affected Components**
- **Immediate Containment Actions Taken**
- **Permanent Invariant & Policy Updates Required**

---

## 3. Seeding Corrective EIPs

To prevent recurrence, the post-mortem automatically seeds a new corrective Executable Intent Package (EIP):

```python
from vad.incident.eip_seeder import EIPSeeder

seeder = EIPSeeder()
corrective_eip = seeder.seed_from_post_mortem(report)

print(f"Generated Corrective EIP: {corrective_eip.name}")
# Sown into planning_artifacts/ for immediate scheduling in the next tranche
```

---

## 4. Closed-Loop CLI Remediation (`vad operations remediate`)

To triage a production or runtime drift incident, link it to an originating run for escaped-defect telemetry, seed a `HIGH` or `CRITICAL` remediation EIP, and execute governed delivery in one command:

```bash
# Seed a HIGH-risk remediation EIP with human release-guardian sign-off
vad operations remediate \
  --db .vad/control-plane/vad.sqlite3 \
  --run-id rem-902 \
  --trigger-type runtime_drift \
  --description "Unauthorized egress rule detected on prod gateway" \
  --originating-run-id run-902 \
  --approver release-guardian-alex

# Escalate to CRITICAL risk with explicit authorization reference and dual sign-off
vad operations remediate \
  --db .vad/control-plane/vad.sqlite3 \
  --run-id rem-903 \
  --trigger-type security_alert \
  --description "Production IAM policy drift" \
  --critical \
  --auth-ref op-intent-2026-11-26 \
  --approver release-guardian-alex \
  --approver release-guardian-sam
```

Candidate lessons extracted during remediation remain advisory until approved by a human release guardian via `vad lessons approve`.

---

## 5. Knowledge Persistence in the Skill Ledger

Lessons learned from incidents are committed to the persistent Skill Ledger (`vad/memory/skill_ledger.py`), allowing future agents to retrieve past failure modes before writing code.
