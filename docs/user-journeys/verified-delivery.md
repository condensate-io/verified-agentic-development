# User Journey: Verified Delivery Flow (Evidence-Driven Autonomy)

This journey follows one change request through VAD's reference **Verified Delivery Flow**. It shows how the same pipeline grants different levels of authority depending on the **risk of the change** and the **proof available**, and how it behaves when things go wrong.

For the module-level reference, see [Verified Delivery Flow](../verified-delivery-flow.md).

---

## 1. Participants

| Role | Who | What they do |
|------|-----|--------------|
| **Requester** | Engineer, ticket, or incident | Raises the change request. |
| **Builder Host** | Codex, Claude Code, Cursor, or a custom agent | Builds the change under a fenced lease. Its output is a *claim*, never proof. |
| **Independent Verifier** | A separate agent identity in an isolated environment | Re-runs the proof obligations on the exact code snapshot. |
| **Release Guardian** | Human operator | Approves High and Critical changes. Cannot be the builder. |
| **Memory Provider** *(optional)* | Condensate Core, Mem0, Graphiti, a custom endpoint, or none | Supplies advisory history. Never decides anything. |

VAD owns every decision. Hosts and memory providers can be swapped without changing the outcome rules.

---

## 2. The Eight Steps from the Requester's Point of View

1. **Request and EIP.** The change is turned into an Executable Intent Package (EIP) with a risk tier, an assurance level, and a freshness policy. Relevant past incidents and *human-approved* lessons are pulled in as advice.
2. **Proof plan.** Each requirement becomes an executable proof obligation. Approved lessons can add regression obligations.
3. **Construction.** The work item is assigned to a builder host with a fenced lease and a role-scoped session token. A durable checkpoint is written.
4. **Independent verification.** A different actor runs the proofs in isolation. VAD checks that the evidence still matches the commit and tree, then signs an Ed25519 attestation.
5. **Policy decision.** The autonomy matrix compares risk, assurance, freshness, and approvals.
6. **Release.** A release decision is recorded with a link to the evidence.
7. **Publish learnings.** The outcome is sent to the memory provider as an observed fact plus, optionally, a *proposed* lesson.
8. **Lesson Promotion.** When a similar task arrives, human-approved lessons (`vad lessons approve`) are retrieved during steps 1 and 2 as executable proof obligations.

---

## 3. What Each Risk Tier Experiences

| Risk | Typical change | Assurance needed | What happens |
|------|----------------|------------------|--------------|
| **Low** | Documentation, isolated test | `A1_BASIC` | Released autonomously once checks pass. |
| **Medium** | Internal API change | `A2_INDEPENDENT` | Released autonomously only after an independent verifier passes. |
| **High** | Authentication, data migration | `A3_ATTESTED` | Blocked as `human_approval_required` until a distinct human guardian approves. |
| **Critical** | Production access, policy change | `A4_RESTRICTED` | Blocked as `restricted_authorization_required` until there is an explicit authorization reference and two distinct human approvers. |

Evidence freshness windows tighten with risk: 24 hours (Low), 4 hours (Medium), 1 hour (High), 15 minutes (Critical).

### Running it

```bash
# Low risk: autonomous after independent verification
vad workflow verified-delivery \
  --db .vad/control-plane/vad.sqlite3 \
  --run-id run-docs-001 --eip-name update-readme \
  --request "Fix typos in the README" --risk low

# High risk: needs a human release guardian who is not the builder
vad workflow verified-delivery \
  --db .vad/control-plane/vad.sqlite3 \
  --run-id run-auth-014 --eip-name token-refresh-recovery \
  --request "Implement token refresh recovery" --risk high \
  --domain-tag auth --approver release-guardian-alex

# Critical risk: explicit authorization plus two guardians
vad workflow verified-delivery \
  --db .vad/control-plane/vad.sqlite3 \
  --run-id run-prod-002 --eip-name rotate-signing-policy \
  --request "Change production signing policy" --risk critical \
  --auth-ref op-intent-2026-11-24 \
  --approver release-guardian-alex --approver release-guardian-sam
```

Use `--memory-provider local --memory-path .vad/memory.json` to enable the local lesson store. The default is `null`, which runs with no memory at all.

---

## 4. When the Change Is Blocked

VAD fails closed. These are the outcomes a requester may see, and what to do next.

| Outcome | Meaning | Next step |
|---------|---------|-----------|
| `human_approval_required` | High risk, no distinct guardian yet. | Ask a release guardian to approve. |
| `restricted_authorization_required` | Critical risk without authorization or dual sign-off. | Provide the authorization reference and two guardians. |
| `blocked_stale_evidence` | Code, tree, environment, or time window changed after verification. | Re-run independent verification. |
| `blocked_insufficient_assurance` | Achieved assurance is below the tier requirement. | Add the missing proofs or independent execution. |
| `blocked_drift` | Drift detected at the runtime or drift stage. | Investigate, then re-verify. |
| `denied_policy` | Self-approval, self-verification, or failed verification. | Use a distinct verifier or approver, or fix the failing proof. |

---

## 5. Surviving Failures

The flow is designed to be resumed, not restarted.

* **Builder host crashes.** The lease expires and the work is reassigned with a higher `fence_token`. If the old host comes back and writes with its old token, it receives `409 Conflict` and nothing changes.
* **Control plane restarts.** State is read from SQLite. The last checkpoint tells VAD the next stage to run, and retried calls with the same `idempotency_key` replay safely.
* **Code changes after verification.** The freshness check fails and release is blocked until verification runs again.
* **Memory provider offline.** VAD continues with empty advice and records the degraded state. Delivery is not blocked.
* **Rogue memory response.** A provider that returns an unapproved lesson as approved, or injects approval fields, is rejected by the authority guard.

---

## 6. Checking the Trail Afterwards

Another operator can verify a run from VAD's ledger alone, without trusting the builder or the memory provider:

```bash
vad audit verify-trail run-auth-014 \
  --db .vad/control-plane/vad.sqlite3 \
  --key-dir .vad/keys
```

The command exits non-zero if any check fails. See [Enterprise Compliance](enterprise-compliance.md) for the full list of checks, and [Learning Loop and Memory](learning-loop-and-memory.md) for how step 7 turns into step 8.
