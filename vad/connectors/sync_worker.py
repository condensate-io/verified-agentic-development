"""Background bidirectional sync worker for external tickets with retry backoff and governance gates."""
from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from vad.connectors.base import BaseWorkConnector
from vad.connectors.models import (
    AttestationSyncPayload,
    ConnectorPlatform,
    ExternalWorkRef,
    OutboundTransitionRequest,
    WorkIntakePayload,
)
from vad.connectors.registry import ConnectorRegistry, get_connector_registry
from vad.control_plane.work_items import WorkItem, WorkItemStatus
from vad.policy.intake_dor_policy import (
    DoREvaluationStatus,
    IntakeDoRPolicy,
    evaluate_intake_dor,
)
from vad.policy.intake_release_gate import (
    IntakeReleaseGate,
    ReleaseGateDecision,
    ReleaseSignoff,
    evaluate_intake_release_gate,
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class SyncJobType(str, Enum):
    INBOUND_POLL = "inbound_poll"
    OUTBOUND_STATUS = "outbound_status"
    OUTBOUND_COMMENT = "outbound_comment"
    OUTBOUND_ATTESTATION = "outbound_attestation"


class SyncJobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    HELD_BY_GOVERNANCE = "held_by_governance"


class SyncJob(BaseModel):
    job_id: str = Field(default_factory=lambda: f"job-{uuid.uuid4().hex[:12]}")
    job_type: SyncJobType
    platform: ConnectorPlatform
    ref: ExternalWorkRef
    status: SyncJobStatus = SyncJobStatus.PENDING
    work_item_id: Optional[str] = None
    payload: Dict[str, Any] = Field(default_factory=dict)
    retry_count: int = 0
    max_retries: int = 3
    error: Optional[str] = None
    created_at: datetime = Field(default_factory=_utc_now)
    completed_at: Optional[datetime] = None


class SyncBatchResult(BaseModel):
    total_jobs: int
    succeeded: int
    failed: int
    held: int
    jobs: List[SyncJob] = Field(default_factory=list)


class SyncWorker:
    """Orchestrates bidirectional sync between VAD control plane and external platform connectors."""

    def __init__(
        self,
        registry: Optional[ConnectorRegistry] = None,
        release_gate: Optional[IntakeReleaseGate] = None,
        dor_policy: Optional[IntakeDoRPolicy] = None,
    ) -> None:
        self.registry = registry or get_connector_registry()
        self.release_gate = release_gate or IntakeReleaseGate()
        self.dor_policy = dor_policy or IntakeDoRPolicy()
        self._queue: List[SyncJob] = []

    def enqueue_job(self, job: SyncJob) -> str:
        self._queue.append(job)
        return job.job_id

    def list_queue(self) -> List[SyncJob]:
        return list(self._queue)

    def sync_once(self) -> SyncBatchResult:
        """Executes a single processing tick over all pending sync jobs."""
        succeeded = 0
        failed = 0
        held = 0
        processed_jobs: List[SyncJob] = []

        pending_jobs = [j for j in self._queue if j.status in (SyncJobStatus.PENDING, SyncJobStatus.RUNNING)]

        for job in pending_jobs:
            connector_name = job.payload.get("connector_name")
            if connector_name:
                connector = self.registry.get(connector_name)
            else:
                conns = self.registry.get_by_platform(job.platform)
                connector = conns[0] if conns else None

            if connector is None:
                job.status = SyncJobStatus.FAILED
                job.error = f"No active connector registered for platform {job.platform.value}"
                failed += 1
                processed_jobs.append(job)
                continue

            try:
                if job.job_type == SyncJobType.OUTBOUND_COMMENT:
                    comment = job.payload.get("comment", "")
                    is_internal = bool(job.payload.get("is_internal", False))
                    ok = connector.post_comment(job.ref, comment, is_internal=is_internal)
                    if ok:
                        job.status = SyncJobStatus.COMPLETED
                        job.completed_at = _utc_now()
                        succeeded += 1
                    else:
                        self._handle_failure(job, "Failed to post comment to remote system")
                        if job.status == SyncJobStatus.FAILED:
                            failed += 1

                elif job.job_type == SyncJobType.OUTBOUND_ATTESTATION:
                    attestation = AttestationSyncPayload(
                        ref=job.ref,
                        evidence_digest=job.payload["evidence_digest"],
                        test_summary=job.payload.get("test_summary", "Verification passed"),
                        status=job.payload.get("status", "verified"),
                        verified=job.payload.get("verified", True),
                        commit_sha=job.payload.get("commit_sha"),
                        diff_url=job.payload.get("diff_url"),
                    )
                    ok = connector.attach_attestation(attestation)
                    if ok:
                        job.status = SyncJobStatus.COMPLETED
                        job.completed_at = _utc_now()
                        succeeded += 1
                    else:
                        self._handle_failure(job, "Failed to attach attestation to remote system")
                        if job.status == SyncJobStatus.FAILED:
                            failed += 1

                elif job.job_type == SyncJobType.OUTBOUND_STATUS:
                    target_status = job.payload.get("target_status", "Done")
                    is_closing = target_status.lower() in ("done", "closed", "resolved", "completed")

                    # If closing the remote ticket, evaluate 2-Person release gate
                    if is_closing:
                        work_item_data = job.payload.get("work_item", {})
                        signoffs_data = job.payload.get("signoffs", [])
                        signoffs = [
                            ReleaseSignoff(**s) if isinstance(s, dict) else s
                            for s in signoffs_data
                        ]
                        gate_res = self.release_gate.evaluate(work_item_data, signoffs=signoffs)

                        if not gate_res.can_close_remote_ticket:
                            job.status = SyncJobStatus.HELD_BY_GOVERNANCE
                            job.error = f"Outbound close held by governance: {'; '.join(gate_res.reasons)}"
                            held += 1
                            processed_jobs.append(job)
                            # Post governance notice comment
                            connector.post_comment(
                                job.ref,
                                f"[VAD Release Gate Notice] Outbound ticket resolution held pending governance checks: {'; '.join(gate_res.reasons)}",
                                is_internal=False,
                            )
                            continue

                    # Execute remote status transition
                    req = OutboundTransitionRequest(
                        ref=job.ref,
                        target_status=target_status,
                        transition_note=job.payload.get("transition_note"),
                        labels_to_add=job.payload.get("labels_to_add", []),
                        labels_to_remove=job.payload.get("labels_to_remove", []),
                        evidence_digest=job.payload.get("evidence_digest"),
                    )
                    ok = connector.sync_status(req)
                    if ok:
                        job.status = SyncJobStatus.COMPLETED
                        job.completed_at = _utc_now()
                        succeeded += 1
                    else:
                        self._handle_failure(job, "Remote status transition returned False")
                        if job.status == SyncJobStatus.FAILED:
                            failed += 1

                elif job.job_type == SyncJobType.INBOUND_POLL:
                    query = job.payload.get("query")
                    items = connector.poll_work_items(query=query)
                    ingested_count = 0
                    for item in items:
                        dor_res = self.dor_policy.evaluate(item)
                        if not dor_res.passed:
                            connector.post_comment(
                                item.ref,
                                f"[VAD Ingress Notice] Ticket failed Definition of Ready: {dor_res.feedback_message}",
                                is_internal=False,
                            )
                        else:
                            ingested_count += 1
                    job.status = SyncJobStatus.COMPLETED
                    job.completed_at = _utc_now()
                    job.payload["ingested_count"] = ingested_count
                    succeeded += 1

            except Exception as e:
                self._handle_failure(job, str(e))
                if job.status == SyncJobStatus.FAILED:
                    failed += 1

            processed_jobs.append(job)

        return SyncBatchResult(
            total_jobs=len(pending_jobs),
            succeeded=succeeded,
            failed=failed,
            held=held,
            jobs=processed_jobs,
        )

    def _handle_failure(self, job: SyncJob, error_msg: str) -> None:
        job.retry_count += 1
        job.error = error_msg
        if job.retry_count >= job.max_retries:
            job.status = SyncJobStatus.FAILED
        else:
            job.status = SyncJobStatus.PENDING
