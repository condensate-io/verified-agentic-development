# User Journey: Learning Loop and Pluggable Memory

This journey explains how VAD learns from past work without letting any memory system control verification, approvals, or the evidence ledger.

**The rule:** memory advises, VAD decides.

---

## 1. Participants

| Role | Responsibility |
|------|----------------|
| **Release Guardian or Operator** | Reviews candidate lessons and approves or rejects them. |
| **Platform Engineer** | Chooses and configures the memory provider. |
| **Agents (builder/verifier)** | Receive advice during planning. They cannot promote lessons. |
| **VAD** | Records facts, stores review decisions, and enforces the authority boundary. |

---

## 2. Choosing a Memory Provider

VAD works with any of these. All are optional, and all are replaceable.

| Provider | Use it when |
|----------|-------------|
| `null` | You want no memory. VAD runs normally. |
| `local` | You want a simple local JSON store. |
| `condensate_core` | You want the reference provider, reachable over HTTP, MCP, or both with automatic failover. |
| `mem0`, `graphiti` | You already run one of these. |
| `custom_http`, `custom_mcp` | You have your own memory service. |

Configuration uses `MemoryProviderConfig`:

```python
from vad.memory.config import MemoryProviderConfig, MemoryProviderKind, MemoryTransport
from vad.memory.gateway import MemoryGateway
from vad.memory.providers.registry import create_memory_provider

config = MemoryProviderConfig(
    provider=MemoryProviderKind.CONDENSATE_CORE,
    transport=MemoryTransport.BOTH,
    endpoint_url="http://127.0.0.1:8000",
    mcp_endpoint="http://127.0.0.1:8765/mcp",
    auth_secret_ref="env:CONDENSATE_API_KEY",   # a reference, never a raw secret
    fail_open_on_unavailable=True,
)
gateway = MemoryGateway(provider=create_memory_provider(config))
```

Details are in [Memory Providers](../memory-providers.md) and [Condensate Core Integration](../condensate-integration.md).

---

## 3. Facts Versus Lessons

VAD keeps two kinds of knowledge strictly apart.

| | Observed fact | Inferred lesson |
|---|---------------|-----------------|
| **What it is** | Something that happened: a verified delivery, a rejected change, an incident, a policy denial. | A suggestion drawn from facts, such as "add a token-expiry proof obligation". |
| **Anchored to** | The evidence digest and EIP digest from VAD's ledger. | The fact IDs it was derived from. |
| **Starts as** | Recorded. | `proposed`. |
| **Can change policy?** | No. | Only after human approval. |

Sensitive text is redacted before anything leaves VAD.

---

## 4. The Loop in Practice

### Step 1: A run finishes
At the end of a delivery run, VAD publishes an observed fact and, if the run produced one, a candidate lesson. If the memory provider is offline, delivery is unaffected.

### Step 2: A guardian reviews candidates

```bash
vad lessons list --db .vad/control-plane/vad.sqlite3 --status proposed
```

### Step 3: Approve or reject

```bash
vad lessons approve lesson-auth-expiry \
  --db .vad/control-plane/vad.sqlite3 \
  --reviewer alice --role release_guardian \
  --note "Matches the regression we saw in run-auth-014"

vad lessons reject lesson-too-broad \
  --db .vad/control-plane/vad.sqlite3 \
  --reviewer alice --role operator --reason "Too broad to apply automatically"
```

Only `release_guardian` and `operator` roles can decide. A builder or agent attempting to approve a lesson is refused. The decision is stored in VAD's own ledger first, then synced to the memory provider if it is reachable.

The same actions are available over HTTP: `GET /lessons`, `POST /lessons/{id}/approve`, and `POST /lessons/{id}/reject`.

### Step 4: A similar task arrives
When a new change request resembles earlier work, VAD retrieves **approved** lessons only. They can:

* add a regression proof obligation to the proof plan,
* add an invariant to the EIP,
* raise the risk tier by one step (never lower it).

Unapproved or rejected lessons are ignored, even if a provider returns them.

---

## 5. What a Memory Provider Can Never Do

* Change a verification result.
* Grant an approval.
* Override a policy decision.
* Modify the evidence ledger.

If a provider tries (for example by returning an unapproved lesson in the approved list, or by injecting an approval field), VAD's authority guard rejects the response and the run's audit check flags it.

---

## 6. Adding Your Own Provider

Any provider must pass the shared conformance suite in `tests/memory_conformance/test_provider_conformance.py`. It checks capability discovery, fact and lesson separation, the approval gate, redaction, and graceful behavior when offline. Register in-process providers with `register_custom_provider()`, or point `custom_http` or `custom_mcp` at your own endpoint.
