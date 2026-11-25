from __future__ import annotations

from pathlib import Path
import subprocess
from typing import Any

from vad.audit.metrics import AutonomyMetricsCalculator
from vad.contracts.models import AssuranceLevel, RiskTier
from vad.control_plane.work_items import WorkItemStatus
from vad.feedback.lesson_review import LessonReviewService
from vad.incident.incident_responder import IncidentSeverity
from vad.memory.contracts import FactType, LessonReviewStatus, ProposedRuleType
from vad.memory.gateway import MemoryGateway
from vad.memory.providers.local import LocalMemoryProvider
from vad.policy.autonomy_matrix import PolicyStage
from vad.release.decision import ReleaseOutcome
from vad.server.db.store import ServerStore
from vad.signing.ed25519_local import LocalEd25519KeyProvider
from vad.workflows.continuous_operations import (
    ContinuousOperationsRequest,
    ContinuousOperationsResult,
    ContinuousVerifiedOperationsWorkflow,
)
from vad.workflows.verified_delivery import (
    VerifiedDeliveryRequest,
    VerifiedDeliveryWorkflow,
)

_SEED_BYTES = bytes.fromhex(
    "9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60"
)


def _make_workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "workspace"
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "service.py").write_text(
        "def handle_request(token_valid: bool) -> str:\n"
        "    return 'ok' if token_valid else 'deny'\n",
        encoding="utf-8",
    )
    (ws / "test_service.py").write_text(
        "from service import handle_request\n\n"
        "def test_handle_request() -> None:\n"
        "    assert handle_request(True) == 'ok'\n",
        encoding="utf-8",
    )
    return ws


def _passing_executor(cmd_parts: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        args=cmd_parts,
        returncode=0,
        stdout=f"ok: {' '.join(cmd_parts)}\n",
        stderr="",
    )


def test_sev2_runtime_anomaly_remediation_defect_link_and_closed_loop_learning(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "ops_sev2.sqlite3"
    store = ServerStore(db_path)
    workspace = _make_workspace(tmp_path)
    key_provider = LocalEd25519KeyProvider.from_seed(
        _SEED_BYTES,
        key_id="ed25519-ops-sev2",
        key_dir=tmp_path / ".vad" / "keys",
    )
    gateway = MemoryGateway(provider=LocalMemoryProvider(local_path=tmp_path / "memory.json"))

    delivery_workflow = VerifiedDeliveryWorkflow(
        store=store,
        memory_gateway=gateway,
        key_provider=key_provider,
        workspace_dir=workspace,
        command_executor=_passing_executor,
    )

    # Originating accepted run that later suffers a runtime SLO / regression anomaly
    originating_res = delivery_workflow.execute(
        VerifiedDeliveryRequest(
            run_id="run-origin-1",
            change_request="Update service token handler so that valid tokens pass.",
            eip_name="service-token-handler",
            risk_tier=RiskTier.MEDIUM,
            domain_tags=["operations"],
            cost_usd=0.04,
        )
    )
    assert originating_res.release_decision.can_release is True
    assert originating_res.release_decision.outcome == ReleaseOutcome.AUTONOMOUS_RELEASE

    ops_workflow = ContinuousVerifiedOperationsWorkflow(
        store=store,
        memory_gateway=gateway,
        key_provider=key_provider,
        workspace_dir=workspace,
        command_executor=_passing_executor,
        audit_sqlite_path=str(tmp_path / "missing_audit.db"),
    )

    remediation_res = ops_workflow.execute(
        ContinuousOperationsRequest(
            remediation_run_id="run-remedy-sev2-1",
            trigger_type="slo_breach",
            description="SEV-2 runtime anomaly: elevated p99 auth latency after token handler deploy",
            originating_run_id="run-origin-1",
            severity_hint=IncidentSeverity.SEV2,
            escalate_critical=False,
            stage=PolicyStage.RUNTIME,
            domain_tags=["incident", "operations"],
            human_approvers=["guardian-alice"],
        )
    )

    assert isinstance(remediation_res, ContinuousOperationsResult)
    assert remediation_res.remediation_run_id == "run-remedy-sev2-1"
    assert remediation_res.incident_report.severity == IncidentSeverity.SEV2
    assert remediation_res.post_mortem.incident_id == remediation_res.incident_report.id
    assert remediation_res.seeded_eip.risk_tier == RiskTier.HIGH
    assert remediation_res.seeded_eip.effective_assurance_level() == AssuranceLevel.A3_ATTESTED

    # DefectLinkRecord persisted against originating accepted run
    assert remediation_res.defect_link is not None
    assert remediation_res.defect_link.run_id == "run-origin-1"
    assert remediation_res.defect_link.evidence_digest == originating_res.evidence_digest
    assert remediation_res.defect_link.incident_id == remediation_res.incident_report.id
    assert remediation_res.defect_link.severity == IncidentSeverity.SEV2.value

    metrics = AutonomyMetricsCalculator().compute_from_sqlite(store.db_path)
    assert metrics.escaped_defect_count == 1
    assert metrics.accepted_changes == 2
    assert metrics.escaped_defect_rate == 0.5

    # Incident ObservedFact published + guardian-approved remediation delivery succeeded
    assert remediation_res.incident_fact.fact_type == FactType.INCIDENT
    assert remediation_res.delivery_result.release_decision.outcome == ReleaseOutcome.APPROVED_WITH_HUMAN
    assert remediation_res.delivery_result.release_decision.can_release is True
    assert remediation_res.delivery_result.audit_report.verified is True
    assert store.load_work_item("wi-run-remedy-sev2-1").status == WorkItemStatus.COMPLETED

    # Candidate lesson recorded as PROPOSED in authoritative store
    lesson_id = remediation_res.candidate_lesson.lesson_id
    assert lesson_id == remediation_res.delivery_result.published_lesson_id
    promo = store.load_lesson_promotion(lesson_id)
    assert promo is not None
    assert promo.review_status == LessonReviewStatus.PROPOSED.value
    assert promo.proposed_rule_type == ProposedRuleType.PROOF_OBLIGATION.value

    # Human guardian approves the remediation lesson via LessonReviewService
    review_service = LessonReviewService(store, memory_gateway=gateway)
    approved_promo = review_service.approve_lesson(
        lesson_id,
        reviewer="guardian-bob",
        reviewer_role="release_guardian",
        note="Enforce regression proof for runtime auth latency anomaly",
    )
    assert approved_promo.review_status == LessonReviewStatus.APPROVED.value

    # Next delivery run automatically retrieves and applies the approved remediation lesson
    next_run = delivery_workflow.execute(
        VerifiedDeliveryRequest(
            run_id="run-followup-2",
            change_request="Refine token handler caching in service module so that latency stays bounded.",
            eip_name="service-token-cache",
            risk_tier=RiskTier.MEDIUM,
            domain_tags=["incident", "operations"],
        )
    )
    assert lesson_id in next_run.applied_lesson_ids
    assert remediation_res.incident_fact.fact_id in next_run.retrieved_fact_ids
    assert remediation_res.delivery_result.published_fact_id is not None
    assert remediation_res.delivery_result.published_fact_id in next_run.retrieved_fact_ids
    assert next_run.audit_report.verified is True

    followup_ev = store.load_run_evidence("run-followup-2").evidence
    assert followup_ev.verification is not None
    obligation_ids = {r["obligation_id"] for r in followup_ev.verification.results}
    assert any(ob.startswith("po-lesson-") for ob in obligation_ids)


def test_sev1_escalated_drift_incident_requires_dual_signoff_and_explicit_auth(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "ops_sev1.sqlite3"
    store = ServerStore(db_path)
    workspace = _make_workspace(tmp_path)
    key_provider = LocalEd25519KeyProvider.from_seed(
        _SEED_BYTES,
        key_id="ed25519-ops-sev1",
        key_dir=tmp_path / ".vad" / "keys",
    )
    gateway = MemoryGateway(provider=LocalMemoryProvider())

    ops_workflow = ContinuousVerifiedOperationsWorkflow(
        store=store,
        memory_gateway=gateway,
        key_provider=key_provider,
        workspace_dir=workspace,
        command_executor=_passing_executor,
        audit_sqlite_path=str(tmp_path / "missing_audit.db"),
    )

    # 1. Missing dual sign-off (only 1 approver, no explicit_authorization_ref) -> blocked
    blocked_single_approver = ops_workflow.execute(
        ContinuousOperationsRequest(
            remediation_run_id="run-sev1-blocked-1",
            trigger_type="runtime_drift",
            description="SEV-1 drift detected on production signing boundary",
            originating_run_id="run-untracked-origin",
            severity_hint=IncidentSeverity.SEV1,
            escalate_critical=True,
            stage=PolicyStage.DRIFT,
            human_approvers=["guardian-alice"],
            explicit_authorization_ref=None,
        )
    )
    assert blocked_single_approver.seeded_eip.risk_tier == RiskTier.CRITICAL
    assert blocked_single_approver.seeded_eip.assurance_level == AssuranceLevel.A4_RESTRICTED
    assert blocked_single_approver.seeded_eip.effective_assurance_level() == AssuranceLevel.A4_RESTRICTED
    assert blocked_single_approver.defect_link is not None
    assert blocked_single_approver.defect_link.evidence_digest == "0" * 64
    assert blocked_single_approver.delivery_result.release_decision.can_release is False
    assert (
        blocked_single_approver.delivery_result.release_decision.outcome
        == ReleaseOutcome.RESTRICTED_AUTHORIZATION_REQUIRED
    )
    assert store.load_work_item("wi-run-sev1-blocked-1").status == WorkItemStatus.WAITING_FOR_HUMAN

    # 2. Two distinct guardians without explicit_authorization_ref -> still blocked
    blocked_no_auth_ref = ops_workflow.execute(
        ContinuousOperationsRequest(
            remediation_run_id="run-sev1-blocked-2",
            trigger_type="runtime_drift",
            description="SEV-1 drift detected on production signing boundary",
            severity_hint=IncidentSeverity.SEV1,
            escalate_critical=True,
            stage=PolicyStage.DRIFT,
            human_approvers=["guardian-alice", "guardian-bob"],
            explicit_authorization_ref=None,
        )
    )
    assert blocked_no_auth_ref.seeded_eip.risk_tier == RiskTier.CRITICAL
    assert blocked_no_auth_ref.delivery_result.release_decision.can_release is False
    assert (
        blocked_no_auth_ref.delivery_result.release_decision.outcome
        == ReleaseOutcome.RESTRICTED_AUTHORIZATION_REQUIRED
    )

    # 3. Two distinct guardians + explicit_authorization_ref -> APPROVED_RESTRICTED
    approved_res = ops_workflow.execute(
        ContinuousOperationsRequest(
            remediation_run_id="run-sev1-approved",
            trigger_type="runtime_drift",
            description="SEV-1 drift detected on production signing boundary",
            severity_hint=IncidentSeverity.SEV1,
            escalate_critical=True,
            stage=PolicyStage.DRIFT,
            human_approvers=["guardian-alice", "guardian-bob"],
            explicit_authorization_ref="CAB-2026-0991",
        )
    )
    assert approved_res.seeded_eip.risk_tier == RiskTier.CRITICAL
    assert approved_res.seeded_eip.assurance_level == AssuranceLevel.A4_RESTRICTED
    assert approved_res.delivery_result.verification_passed is True
    assert approved_res.delivery_result.freshness_passed is True
    assert approved_res.delivery_result.release_decision.can_release is True
    assert (
        approved_res.delivery_result.release_decision.outcome
        == ReleaseOutcome.APPROVED_RESTRICTED
    )
    assert approved_res.delivery_result.release_decision.recorded_approvers == [
        "guardian-alice",
        "guardian-bob",
    ]
    assert (
        approved_res.delivery_result.release_decision.explicit_authorization_ref
        == "CAB-2026-0991"
    )
    assert approved_res.delivery_result.audit_report.verified is True
    assert store.load_work_item("wi-run-sev1-approved").status == WorkItemStatus.COMPLETED
