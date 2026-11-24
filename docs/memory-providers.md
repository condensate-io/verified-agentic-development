# Memory Providers & Interface v1

VAD is **memory-agnostic**: it operates cleanly with zero external memory (`NullMemoryProvider`) or with any conformant memory backend behind `MemoryGateway` (`vad.memory.gateway.MemoryGateway`) and `MemoryProviderProtocol` (`vad.memory.provider.MemoryProviderProtocol`).

All memory providers are strictly **advisory** and **interchangeable**. For the security invariants separating authoritative VAD evidence from memory state, see [authority-boundaries.md](file:///c:/LocalProjects/condensate-arch-docs/verified-agentic-development/docs/architecture/authority-boundaries.md).

---

## Memory Provider Interface v1 (`MEMORY_INTERFACE_VERSION = "1.0.0"`)

Defined in `vad.memory.contracts` and `vad.memory.provider`, Interface v1 standardizes:

### Protocol (`MemoryProviderProtocol`)

```python
@runtime_checkable
class MemoryProviderProtocol(Protocol):
    name: str
    def capabilities(self) -> MemoryCapabilities: ...
    def health(self) -> ProviderHealth: ...
    def record_fact(self, fact: ObservedFact) -> str: ...
    def record_lesson(self, lesson: InferredLesson) -> str: ...
    def update_lesson_status(
        self,
        lesson_id: str,
        status: LessonReviewStatus,
        reviewed_by: str,
        promotion_record_id: str | None = None,
    ) -> InferredLesson: ...
    def query(self, query: MemoryQuery) -> AdvisoryContext: ...
    def list_facts(self, limit: int = 50) -> list[ObservedFact]: ...
    def list_lessons(
        self,
        status: LessonReviewStatus | None = None,
        limit: int = 50,
    ) -> list[InferredLesson]: ...
```

### Core Data Contracts (`vad.memory.contracts`)

| Model / Enum | Purpose | Key Invariants |
|---|---|---|
| `MEMORY_INTERFACE_VERSION` | `"1.0.0"` | Advertised in every `MemoryCapabilities` and `AdvisoryContext`. |
| `ObservedFact` | Immutable, redacted projection of a VAD outcome (`FactType`: `verified_delivery`, `rejected_change`, `verification_failure`, `incident`, `policy_denial`) | `authoritative_source` is locked to `"vad"`; `source_evidence_digest` must be a 64-char hex SHA-256 digest. |
| `InferredLesson` | Candidate recommendation (`ProposedRuleType`: `proof_obligation`, `invariant`, `risk_calibration`, `tool_restriction`, `general_guidance`) | Tracks `review_status` (`LessonReviewStatus`: `proposed`, `under_review`, `approved`, `rejected`, `superseded`). `is_policy_eligible` is `True` only when `review_status == "approved"` and `reviewed_by` is non-empty. |
| `MemoryQuery` | Scoped retrieval request (`query_text`, `domain_tags`, `fact_types`, `include_unapproved_lessons=False`, `limit=10`) | Defaults to excluding unapproved lessons (`include_unapproved_lessons=False`). |
| `AdvisoryContext` | Retrieval response (`provider_name`, `interface_version`, `facts`, `approved_lessons`, `advisory_unapproved_lessons`, `degraded`, `degraded_reason`) | `advisory_only` is locked to `True`. Unapproved lessons never appear in `approved_lessons`. |

---

## Capability Discovery (`MemoryCapabilities` & `ProviderHealth`)

Before routing queries or displaying provider status, callers inspect `gateway.capabilities()` (`vad.memory.capabilities.MemoryCapabilities`) and `gateway.health()` (`ProviderHealth`):

| Field | Default | Meaning |
|---|---|---|
| `interface_version` | `"1.0.0"` | Interface contract version (`MEMORY_INTERFACE_VERSION`). |
| `fact_storage` | `True` | Provider persists `ObservedFact` records. |
| `lesson_storage` | `True` | Provider persists `InferredLesson` records. |
| `scoped_query` | `True` | Provider supports `MemoryQuery` filtering by tags/types. |
| `health_check` | `True` | Provider implements `health() -> ProviderHealth`. |
| `semantic_search` | `False` | Optional semantic/vector similarity search. |
| `causal_graph` | `False` | Optional causal graph linking facts, incidents, and lessons. |
| `contradiction_resolution` | `False` | Optional detection/resolution of conflicting lessons. |
| `context_consolidation` | `False` | Optional background synthesis/consolidation of episodes. |
| `provenance_traversal` | `False` | Optional multi-hop provenance traversal across evidence links. |
| `supported_transports` | `["in_process"]` | Supported transports (`"in_process"`, `"http"`, `"mcp"`, `"both"`). |

`ProviderHealth` reports `healthy: bool`, `provider_name: str`, `latency_ms: float | None`, and `detail: str`.

---

## Supported Provider Kinds (`MemoryProviderKind`)

`MemoryProviderConfig` (`vad.memory.config`) and `create_memory_provider()` (`vad.memory.providers.registry`) support all built-in provider kinds plus custom provider registration:

| `MemoryProviderKind` | Adapter Class | Transports | Notable Capabilities & Behavior |
|---|---|---|---|
| `null` | `NullMemoryProvider` | `in_process` | No-op provider proving VAD operates cleanly with zero external memory (`fact_storage=False`, `lesson_storage=False`, `scoped_query=False`). |
| `local` | `LocalMemoryProvider` | `in_process` | In-memory or local JSON store (`local_path`), enforcing strict fact-vs-lesson separation offline. |
| `condensate_core` | `CondensateCoreProvider` | `http`, `mcp`, `both` | Reference provider supporting automatic HTTP→MCP failover and all optional advanced capabilities (`semantic_search`, `causal_graph`, `contradiction_resolution`, `context_consolidation`, `provenance_traversal`). See [condensate-integration.md](file:///c:/LocalProjects/condensate-arch-docs/verified-agentic-development/docs/condensate-integration.md). |
| `mem0` | `Mem0MemoryProvider` | `http` | Maps facts and lessons to Mem0 memories (`/health`, `/v1/memories/`, `/v1/memories/search/`) with `record_kind` isolation and `semantic_search=True`. |
| `graphiti` | `GraphitiMemoryProvider` | `http`, `mcp` (`both` failover) | Maps facts and lessons to temporal graph episodes (`entity_type` `"vad_observed_fact"` / `"vad_inferred_lesson"`), advertising `semantic_search=True`, `causal_graph=True`, `provenance_traversal=True`. |
| `custom_http` | `GenericEndpointMemoryProvider` | `http`, `both` | Connects any external HTTP(S) REST memory target using `endpoint_url` and `extra_endpoints`. |
| `custom_mcp` | `GenericEndpointMemoryProvider` | `mcp`, `both` | Connects any external MCP JSON-RPC memory target using `mcp_endpoint` and `extra_endpoints`. |

### Registering Custom In-Process Providers (`register_custom_provider()`)

To register a custom in-process `MemoryProviderProtocol` factory at runtime, use `vad.memory.providers.registry`:

```python
from vad.memory.config import MemoryProviderConfig, MemoryProviderKind
from vad.memory.providers.registry import (
    create_memory_provider,
    list_custom_providers,
    register_custom_provider,
    unregister_custom_provider,
)

def build_my_provider(config: MemoryProviderConfig):
    return MyCustomMemoryAdapter(project_id=config.project_id, **config.options)

register_custom_provider("my_memory", build_my_provider)

provider = create_memory_provider(
    MemoryProviderConfig(
        provider=MemoryProviderKind.LOCAL,
        options={"custom_provider": "my_memory"},
    )
)
```

`create_memory_provider()` checks `config.options.get("custom_provider")` first, then `config.provider.value` in the custom registry, before dispatching to built-in provider classes. Use `list_custom_providers()` and `unregister_custom_provider(name)` to inspect or remove registered factories.

---

## Connecting Arbitrary External HTTP or MCP Endpoints (`GenericEndpointMemoryProvider`)

`GenericEndpointMemoryProvider` (`vad.memory.providers.generic_endpoint`) lets teams connect any external HTTP REST or MCP JSON-RPC memory target without writing a new Python adapter class.

### Default HTTP Routes and MCP Tool Names

| Operation | Default HTTP Route (`custom_http`) | Default MCP Tool (`custom_mcp`) |
|---|---|---|
| Health check | `GET /health` | `memory_health` |
| Record fact | `POST /facts` | `memory_record_fact` |
| List facts | `GET /facts` | `memory_list_facts` |
| Record lesson | `POST /lessons` | `memory_record_lesson` |
| List lessons | `GET /lessons` | `memory_list_lessons` |
| Update lesson status | `PATCH /lessons/{lesson_id}/status` | `memory_update_lesson_status` |
| Query advisory context | `POST /query` | `memory_query` |

### Configuring Custom Endpoints, Auth, and Capability Hints

```python
from vad.memory.config import MemoryProviderConfig, MemoryProviderKind, MemoryTransport
from vad.memory.gateway import MemoryGateway
from vad.memory.providers import create_memory_provider

# Example A: Arbitrary external HTTP memory service
http_config = MemoryProviderConfig(
    provider=MemoryProviderKind.CUSTOM_HTTP,
    transport=MemoryTransport.HTTP,
    endpoint_url="https://memory.internal.example",
    project_id="payments-service",
    auth_secret_ref="env:CUSTOM_MEMORY_TOKEN",
    timeout_seconds=5.0,
    fail_open_on_unavailable=True,
    extra_endpoints={
        "health": "/v2/status",
        "facts": "/v2/observed-facts",
        "lessons": "/v2/lessons",
        "lesson_status": "/v2/lessons/{lesson_id}/review",
        "query": "/v2/retrieve",
    },
    options={
        "provider_name": "internal_rag_memory",
        "semantic_search": True,
    },
)

# Example B: Arbitrary external MCP memory target
mcp_config = MemoryProviderConfig(
    provider=MemoryProviderKind.CUSTOM_MCP,
    transport=MemoryTransport.MCP,
    mcp_endpoint="http://127.0.0.1:8091/mcp",
    project_id="payments-service",
    auth_secret_ref="file:/run/secrets/memory_token",
    extra_endpoints={
        "mcp_query": "team_memory_search",
        "mcp_record_fact": "team_memory_add_fact",
    },
)

gateway = MemoryGateway(provider=create_memory_provider(http_config), fail_open=True)
```

`GenericEndpointMemoryProvider` enforces strict parsing (`parse_observed_fact()`, `parse_inferred_lesson()`, and `classify_lessons()`) so fact and lesson records never cross-contaminate and unapproved lessons never enter `AdvisoryContext.approved_lessons`. `MemoryProviderConfig` validates that `auth_secret_ref` always starts with `env:`, `file:`, `vault:`, or `kms:` and rejects raw secrets.

---

## Shared Conformance Test Suite

Every memory provider adapter integrated with VAD must pass the parameterized conformance suite at [test_provider_conformance.py](file:///c:/LocalProjects/condensate-arch-docs/verified-agentic-development/tests/memory_conformance/test_provider_conformance.py).

### What the Conformance Suite Proves

1. **Protocol & Capability Discovery**: Provider satisfies `isinstance(provider, MemoryProviderProtocol)`, returns `MemoryCapabilities` with `interface_version == MEMORY_INTERFACE_VERSION`, and returns a valid `ProviderHealth`.
2. **Fact vs. Lesson Separation & Approval Gating**: Recording an `ObservedFact` and a `PROPOSED` `InferredLesson` returns facts in `AdvisoryContext.facts` while keeping `AdvisoryContext.approved_lessons` empty until `MemoryGateway.promote_lesson(..., status=LessonReviewStatus.APPROVED, reviewed_by=...)` is executed.
3. **PII / Secret Redaction & Authority Guard**: `MemoryGateway` redacts sensitive strings (such as internal emails or credentials) before outbound storage, and `MemoryAuthorityGuard` raises `MemoryAuthorityViolationError` if any provider attempts to inject forbidden authority fields (`approval_granted`, `verification_override`, `policy_override`, `evidence_ledger_mutation`, `release_approved`, `bypass_gate`).
4. **Zero-Memory Operation**: `NullMemoryProvider` satisfies `MemoryGateway` reads and writes cleanly with `degraded=False`.
5. **Graceful Offline Degradation**: When an external endpoint raises a connection error and `fail_open_on_unavailable=True` / `fail_open=True`, `MemoryGateway.retrieve_advisory_context()` returns an empty `AdvisoryContext(degraded=True, advisory_only=True)` without crashing VAD.

### Running and Extending the Suite

Run the conformance suite locally:

```bash
pytest tests/memory_conformance/test_provider_conformance.py -v
```

To onboard a new provider adapter:
1. Implement `MemoryProviderProtocol` (or connect an endpoint via `GenericEndpointMemoryProvider` / `register_custom_provider()`).
2. Add a builder entry in `_build_provider()` and include the provider key in `ALL_PROVIDERS` in [test_provider_conformance.py](file:///c:/LocalProjects/condensate-arch-docs/verified-agentic-development/tests/memory_conformance/test_provider_conformance.py).
3. Verify all parameterized conformance tests pass, including offline degradation when the backend is unavailable.
