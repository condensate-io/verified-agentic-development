"""Stage 04 Continuous Verified Operations & Incident-to-EIP Remediation Workflow."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from pydantic import BaseModel, Field

from vad.contracts.models import EIP
from vad.incident.eip_seeder import EIPSeeder
from vad.incident.incident_responder import (
    IncidentReport,
    IncidentResponder,
    IncidentSeverity,
    IncidentTrigger,
)
from vad.incident.post_mortem import PostMortem, PostMortemGenerator
from vad.memory.contracts import InferredLesson, ObservedFact
from vad.memory.gateway import MemoryGateway
from vad.memory.providers.null import NullMemoryProvider
from vad.policy.autonomy_matrix import PolicyStage
from vad.server.db.store import DefectLinkRecord, ServerStore
from vad.signing.ed25519_local import LocalEd25519KeyProvider
from vad.workflows.verified_delivery import (
    VerifiedDeliveryRequest,
    VerifiedDeliveryResult,
    VerifiedDeliveryWorkflow,
)


class ContinuousOperationsRequest(BaseModel):
    """Input specification for an incident-to-remediation Continuous Verified Operations run."""

    remediation_run_id: str
    trigger_type: str
    description: str
    originating_run_id: str | None = None
    originating_evidence_digest: str | None = None
    severity_hint: IncidentSeverity | None = None
    escalate_critical: bool = False
    stage: PolicyStage = PolicyStage.RUNTIME
    domain_tags: list[str] = Field(default_factory=lambda: ["incident", "operations"])
    builder_actor: str = "remediation-builder-1"
    verifier_actor: str = "isolated-verifier-1"
    human_approvers: list[str] = Field(default_factory=list)
    explicit_authorization_ref: str | None = None
    cost_usd: float = 0.08
    human_review_seconds: float | None = 45.0


class ContinuousOperationsResult(BaseModel):
    """Structured result emitted by ContinuousVerifiedOperationsWorkflow."""

    remediation_run_id: str
    incident_report: IncidentReport
    post_mortem: PostMortem
    seeded_eip: EIP
    defect_link: DefectLinkRecord | None = None
    incident_fact: ObservedFact
    candidate_lesson: InferredLesson
    delivery_result: VerifiedDeliveryResult


class ContinuousVerifiedOperationsWorkflow:
    """Connects runtime/drift incidents to triage, post-mortem, defect linking, EIP seeding, and remediation delivery."""

    def __init__(
        self,
        store: ServerStore,
        *,
        memory_gateway: MemoryGateway | None = None,
        key_provider: LocalEd25519KeyProvider | None = None,
        workspace_dir: Path | None = None,
        command_executor: Callable | None = None,
        audit_sqlite_path: str = "audit/audit.db",
    ) -> None:
        self.store = store
        self.memory_gateway = (
            memory_gateway
            if memory_gateway is not None
            else MemoryGateway(provider=NullMemoryProvider())
        )
        self.responder = IncidentResponder(sqlite_path=audit_sqlite_path)
        self.pm_generator = PostMortemGenerator()
        self.seeder = EIPSeeder()
        self.delivery_workflow = VerifiedDeliveryWorkflow(
            store=store,
            memory_gateway=self.memory_gateway,
            key_provider=key_provider,
            workspace_dir=workspace_dir,
            command_executor=command_executor,
        )

    def _resolve_originating_digest(
        self,
        originating_run_id: str,
        explicit_digest: str | None,
    ) -> str:
        if explicit_digest is not None:
            return explicit_digest
        try:
            return self.store.load_run_evidence(originating_run_id).evidence_digest
        except KeyError:
            return "0" * 64

    def execute(self, request: ContinuousOperationsRequest) -> ContinuousOperationsResult:
        """Triage an operational incident, seed a remediation EIP, and execute governed delivery."""
        # 1. Triage incident
        incident_report = self.responder.triage(
            IncidentTrigger(
                trigger_type=request.trigger_type,
                description=request.description,
                severity_hint=request.severity_hint,
            )
        )

        # 2. Generate formal post-mortem
        post_mortem = self.pm_generator.generate(incident_report)

        # 3. Seed remediation EIP (escalating to CRITICAL when requested or SEV-1)
        is_critical = request.escalate_critical or (
            incident_report.severity == IncidentSeverity.SEV1
        )
        seeded_eip = self.seeder.seed_from_post_mortem(
            post_mortem,
            critical=is_critical,
        )

        # 4. Extract incident ObservedFact and candidate PROPOSED InferredLesson
        incident_fact, candidate_lesson = self.seeder.extract_incident_fact_and_lesson(
            post_mortem,
            run_id=request.remediation_run_id,
            eip=seeded_eip,
        )

        # 5. Link escaped defect to originating accepted run if specified
        defect_link: DefectLinkRecord | None = None
        if request.originating_run_id:
            orig_digest = self._resolve_originating_digest(
                request.originating_run_id,
                request.originating_evidence_digest,
            )
            defect_link = self.store.record_defect_link(
                DefectLinkRecord(
                    run_id=request.originating_run_id,
                    evidence_digest=orig_digest,
                    incident_id=incident_report.id,
                    severity=incident_report.severity.value,
                    summary=request.description,
                )
            )

        # 6. Publish the incident ObservedFact to memory gateway
        self.memory_gateway.publish_fact(incident_fact)

        # 7. Execute governed remediation delivery
        combined_domain_tags = list(
            dict.fromkeys([*request.domain_tags, *incident_fact.domain_tags])
        )
        delivery_result = self.delivery_workflow.execute(
            VerifiedDeliveryRequest(
                run_id=request.remediation_run_id,
                change_request=seeded_eip.goal.description,
                eip_name=seeded_eip.name,
                risk_tier=seeded_eip.risk_tier,
                domain_tags=combined_domain_tags,
                builder_actor=request.builder_actor,
                verifier_actor=request.verifier_actor,
                human_approvers=list(request.human_approvers),
                explicit_authorization_ref=request.explicit_authorization_ref,
                stage=request.stage,
                cost_usd=request.cost_usd,
                human_review_seconds=request.human_review_seconds,
            ),
            candidate_lesson_title=candidate_lesson.title,
            candidate_lesson_recommendation=candidate_lesson.recommendation,
            candidate_rule_type=candidate_lesson.proposed_rule_type,
        )

        # 8. Sync candidate_lesson ID with the published LessonPromotionRecord ID
        if delivery_result.published_lesson_id:
            candidate_lesson = candidate_lesson.model_copy(
                update={"lesson_id": delivery_result.published_lesson_id}
            )

        # 9. Return complete continuous operations result
        return ContinuousOperationsResult(
            remediation_run_id=request.remediation_run_id,
            incident_report=incident_report,
            post_mortem=post_mortem,
            seeded_eip=seeded_eip,
            defect_link=defect_link,
            incident_fact=incident_fact,
            candidate_lesson=candidate_lesson,
            delivery_result=delivery_result,
        )
