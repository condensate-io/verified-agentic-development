from datetime import timezone
import pytest
from pydantic import ValidationError

from vad.memory.capabilities import MemoryCapabilities, ProviderHealth
from vad.memory.config import (
    MemoryProviderConfig,
    MemoryProviderKind,
    MemoryTransport,
)
from vad.memory.contracts import (
    MEMORY_INTERFACE_VERSION,
    AdvisoryContext,
    FactType,
    InferredLesson,
    LessonReviewStatus,
    MemoryEntry,
    MemoryQuery,
    MemoryScope,
    ObservedFact,
    ProposedRuleType,
)
from vad.memory.provider import MemoryProviderProtocol


VALID_DIGEST = "a" * 64


def test_legacy_memory_scope_and_entry_preserved():
    scope = MemoryScope(owner="agent-1", purpose="notes")
    assert scope.max_payload_size == 1024
    assert MemoryScope.RETROSPECTIVE.owner == "system"
    assert MemoryScope.RETROSPECTIVE.purpose == "retrospective"
    assert MemoryScope.RETROSPECTIVE.max_payload_size == 8192

    entry = MemoryEntry(id="entry-1", scope=scope, content="hello")
    assert entry.metadata == {}
    assert entry.scope == scope


def test_memory_interface_version_and_observed_fact_validation():
    assert MEMORY_INTERFACE_VERSION == "1.0.0"

    fact = ObservedFact(
        fact_id="fact-1",
        fact_type=FactType.VERIFIED_DELIVERY,
        run_id="run-1",
        eip_digest="b" * 64,
        source_evidence_digest=VALID_DIGEST.upper(),
        risk_tier="medium",
        summary="Verified delivery passed all gates",
    )
    assert fact.source_evidence_digest == VALID_DIGEST
    assert fact.authoritative_source == "vad"
    assert fact.observed_at.tzinfo == timezone.utc
    assert fact.domain_tags == []
    assert fact.metadata == {}

    for invalid_digest in ("abc", "z" * 64, "a" * 63, "a" * 65):
        with pytest.raises(ValidationError):
            ObservedFact(
                fact_id="fact-bad",
                fact_type=FactType.INCIDENT,
                run_id="run-1",
                eip_digest="b" * 64,
                source_evidence_digest=invalid_digest,
                risk_tier="high",
                summary="Invalid digest",
            )

    with pytest.raises(ValidationError):
        ObservedFact(
            fact_id="fact-bad-source",
            fact_type=FactType.POLICY_DENIAL,
            run_id="run-1",
            eip_digest="b" * 64,
            source_evidence_digest=VALID_DIGEST,
            risk_tier="high",
            summary="Wrong source",
            authoritative_source="external",  # type: ignore[arg-type]
        )


def test_inferred_lesson_approval_invariant_and_policy_eligibility():
    proposed = InferredLesson(
        lesson_id="lesson-1",
        title="Require integration test",
        recommendation="Add integration check for migrations",
    )
    assert proposed.proposed_rule_type == ProposedRuleType.GENERAL_GUIDANCE
    assert proposed.review_status == LessonReviewStatus.PROPOSED
    assert proposed.confidence == 0.5
    assert proposed.is_policy_eligible is False

    for status in (
        LessonReviewStatus.UNDER_REVIEW,
        LessonReviewStatus.REJECTED,
        LessonReviewStatus.SUPERSEDED,
    ):
        lesson = InferredLesson(
            lesson_id="lesson-non-approved",
            title="Lesson",
            recommendation="Do X",
            review_status=status,
            reviewed_by="alice",
        )
        assert lesson.is_policy_eligible is False

    for bad_reviewer in (None, "", "   "):
        with pytest.raises(ValidationError, match="reviewed_by"):
            InferredLesson(
                lesson_id="lesson-missing-reviewer",
                title="Unreviewed approval",
                recommendation="Do Y",
                review_status=LessonReviewStatus.APPROVED,
                reviewed_by=bad_reviewer,
            )

    approved = InferredLesson(
        lesson_id="lesson-2",
        title="Enforce proof obligation",
        recommendation="Require schema diff check",
        proposed_rule_type=ProposedRuleType.PROOF_OBLIGATION,
        derived_from_fact_ids=["fact-1"],
        domain_tags=["migrations"],
        confidence=0.92,
        review_status=LessonReviewStatus.APPROVED,
        reviewed_by="sec-lead",
        promotion_record_id="promo-1",
    )
    assert approved.is_policy_eligible is True


def test_memory_query_and_advisory_context_contracts():
    query = MemoryQuery(query_text="migration failures")
    assert query.limit == 10
    assert query.include_unapproved_lessons is False

    with pytest.raises(ValidationError):
        MemoryQuery(query_text="bad", limit=0)
    with pytest.raises(ValidationError):
        MemoryQuery(query_text="bad", limit=101)

    context = AdvisoryContext(provider_name="null")
    assert context.interface_version == MEMORY_INTERFACE_VERSION
    assert context.advisory_only is True
    assert context.degraded is False
    assert context.facts == []
    assert context.approved_lessons == []
    assert context.advisory_unapproved_lessons == []

    with pytest.raises(ValidationError):
        AdvisoryContext(provider_name="null", advisory_only=False)  # type: ignore[arg-type]


def test_memory_capabilities_and_provider_health():
    caps = MemoryCapabilities()
    assert caps.interface_version == MEMORY_INTERFACE_VERSION
    assert caps.fact_storage is True
    assert caps.lesson_storage is True
    assert caps.scoped_query is True
    assert caps.health_check is True
    assert caps.semantic_search is False
    assert caps.causal_graph is False
    assert caps.contradiction_resolution is False
    assert caps.context_consolidation is False
    assert caps.provenance_traversal is False
    assert caps.supported_transports == ["in_process"]

    health = ProviderHealth(healthy=True, provider_name="local", latency_ms=1.2, detail="ok")
    assert health.healthy is True
    assert health.provider_name == "local"
    assert health.latency_ms == 1.2


@pytest.mark.parametrize(
    "kind,transport,kwargs",
    [
        (MemoryProviderKind.NULL, MemoryTransport.IN_PROCESS, {}),
        (MemoryProviderKind.LOCAL, MemoryTransport.IN_PROCESS, {"local_path": ".vad/memory"}),
        (
            MemoryProviderKind.CONDENSATE_CORE,
            MemoryTransport.HTTP,
            {"endpoint_url": "http://127.0.0.1:8000"},
        ),
        (
            MemoryProviderKind.CONDENSATE_CORE,
            MemoryTransport.MCP,
            {"mcp_endpoint": "stdio://condensate-mcp"},
        ),
        (
            MemoryProviderKind.CONDENSATE_CORE,
            MemoryTransport.BOTH,
            {"endpoint_url": "http://127.0.0.1:8000"},
        ),
        (
            MemoryProviderKind.CONDENSATE_CORE,
            MemoryTransport.BOTH,
            {"mcp_endpoint": "http://127.0.0.1:8765/mcp"},
        ),
        (MemoryProviderKind.MEM0, MemoryTransport.HTTP, {"endpoint_url": "http://127.0.0.1:8010"}),
        (MemoryProviderKind.GRAPHITI, MemoryTransport.HTTP, {"endpoint_url": "http://localhost:8000"}),
        (
            MemoryProviderKind.CUSTOM_HTTP,
            MemoryTransport.HTTP,
            {"endpoint_url": "http://127.0.0.1:8090/memory"},
        ),
        (
            MemoryProviderKind.CUSTOM_MCP,
            MemoryTransport.MCP,
            {"mcp_endpoint": "python -m custom_mcp"},
        ),
    ],
)
def test_memory_provider_config_valid_variants(kind, transport, kwargs):
    cfg = MemoryProviderConfig(provider=kind, transport=transport, **kwargs)
    assert cfg.provider == kind
    assert cfg.transport == transport
    assert cfg.fail_open_on_unavailable is True
    assert cfg.project_id == "default"


@pytest.mark.parametrize(
    "kind,transport,kwargs",
    [
        (MemoryProviderKind.CONDENSATE_CORE, MemoryTransport.HTTP, {}),
        (MemoryProviderKind.CONDENSATE_CORE, MemoryTransport.MCP, {}),
        (MemoryProviderKind.CONDENSATE_CORE, MemoryTransport.BOTH, {}),
        (MemoryProviderKind.CUSTOM_HTTP, MemoryTransport.HTTP, {}),
        (MemoryProviderKind.CUSTOM_MCP, MemoryTransport.MCP, {}),
    ],
)
def test_memory_provider_config_missing_required_endpoints(kind, transport, kwargs):
    with pytest.raises(ValidationError):
        MemoryProviderConfig(provider=kind, transport=transport, **kwargs)


def test_memory_provider_config_secret_ref_validation():
    for valid_ref in (
        "env:CONDENSATE_API_KEY",
        "file:/run/secrets/condensate_token",
        "vault:secret/data/vad/memory#token",
        "kms:alias/vad-memory-key",
    ):
        cfg = MemoryProviderConfig(auth_secret_ref=valid_ref)
        assert cfg.auth_secret_ref == valid_ref

    for invalid_ref in (
        "",
        "   ",
        "env:",
        "sk-1234567890",
        "ghp_secretvalue",
        "Bearer abc123",
        "raw-token-without-prefix",
        "env:BAD\nKEY",
        "env:BAD\x00KEY",
        "api_key=supersecret",
    ):
        with pytest.raises(ValidationError):
            MemoryProviderConfig(auth_secret_ref=invalid_ref)


def test_memory_provider_protocol_runtime_checkable():
    class DummyProvider:
        name = "dummy"

        def capabilities(self) -> MemoryCapabilities:
            return MemoryCapabilities()

        def health(self) -> ProviderHealth:
            return ProviderHealth(healthy=True, provider_name=self.name)

        def record_fact(self, fact: ObservedFact) -> str:
            return fact.fact_id

        def record_lesson(self, lesson: InferredLesson) -> str:
            return lesson.lesson_id

        def update_lesson_status(
            self,
            lesson_id: str,
            status: LessonReviewStatus,
            reviewed_by: str,
            promotion_record_id: str | None = None,
        ) -> InferredLesson:
            return InferredLesson(
                lesson_id=lesson_id,
                title="Dummy",
                recommendation="Dummy",
                review_status=status,
                reviewed_by=reviewed_by,
                promotion_record_id=promotion_record_id,
            )

        def query(self, query: MemoryQuery) -> AdvisoryContext:
            return AdvisoryContext(provider_name=self.name)

        def list_facts(self, limit: int = 50) -> list[ObservedFact]:
            return []

        def list_lessons(
            self,
            status: LessonReviewStatus | None = None,
            limit: int = 50,
        ) -> list[InferredLesson]:
            return []

    class IncompleteProvider:
        name = "incomplete"

    assert isinstance(DummyProvider(), MemoryProviderProtocol)
    assert not isinstance(IncompleteProvider(), MemoryProviderProtocol)
