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

## 4. Knowledge Persistence in the Skill Ledger

Lessons learned from incidents are committed to the persistent Skill Ledger (`vad/memory/skill_ledger.py`), allowing future agents to retrieve past failure modes before writing code.
