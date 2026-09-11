import pytest
from unittest.mock import MagicMock
from vad.connectors.base import BaseWorkConnector
from vad.connectors.models import (
    ConnectorConfig,
    ConnectorPlatform,
    ExternalWorkRef,
    WorkIntakePayload,
)
from vad.connectors.registry import ConnectorRegistry
from vad.connectors.sync_worker import (
    SyncJob,
    SyncJobStatus,
    SyncJobType,
    SyncWorker,
)


class MockConnector(BaseWorkConnector):
    def __init__(self, platform=ConnectorPlatform.GITHUB):
        super().__init__(
            ConnectorConfig(
                name="mock-conn",
                platform=platform,
                instance_url="https://github.com",
            )
        )
        self.comments = []
        self.attestations = []
        self.transitions = []
        self.poll_items = []
        self.fail_next = False

    def validate_webhook(self, headers, body):
        return True

    def parse_webhook_event(self, headers, payload):
        return None

    def poll_work_items(self, query=None):
        return self.poll_items

    def sync_status(self, transition):
        if self.fail_next:
            raise RuntimeError("Transient connection error")
        self.transitions.append(transition)
        return True

    def post_comment(self, ref, comment, is_internal=False):
        self.comments.append((ref, comment, is_internal))
        return True

    def attach_attestation(self, attestation):
        self.attestations.append(attestation)
        return True


def _sample_ref():
    return ExternalWorkRef(
        platform=ConnectorPlatform.GITHUB,
        instance_url="https://github.com",
        remote_id="101",
        remote_key="#101",
    )


def test_sync_worker_processes_outbound_comment():
    registry = ConnectorRegistry()
    mock_conn = MockConnector()
    registry.register(mock_conn)
    worker = SyncWorker(registry=registry)

    ref = _sample_ref()
    job = SyncJob(
        job_type=SyncJobType.OUTBOUND_COMMENT,
        platform=ConnectorPlatform.GITHUB,
        ref=ref,
        payload={"comment": "Agent progress: 50% completed"},
    )
    worker.enqueue_job(job)
    batch = worker.sync_once()

    assert batch.succeeded == 1
    assert batch.failed == 0
    assert len(mock_conn.comments) == 1
    assert "Agent progress: 50%" in mock_conn.comments[0][1]


def test_sync_worker_processes_outbound_attestation():
    registry = ConnectorRegistry()
    mock_conn = MockConnector()
    registry.register(mock_conn)
    worker = SyncWorker(registry=registry)

    ref = _sample_ref()
    job = SyncJob(
        job_type=SyncJobType.OUTBOUND_ATTESTATION,
        platform=ConnectorPlatform.GITHUB,
        ref=ref,
        payload={
            "evidence_digest": "c" * 64,
            "test_summary": "All 25 unit tests passed",
        },
    )
    worker.enqueue_job(job)
    batch = worker.sync_once()

    assert batch.succeeded == 1
    assert len(mock_conn.attestations) == 1
    assert mock_conn.attestations[0].evidence_digest == "c" * 64


def test_sync_worker_holds_outbound_close_if_release_gate_fails():
    registry = ConnectorRegistry()
    mock_conn = MockConnector()
    registry.register(mock_conn)
    worker = SyncWorker(registry=registry)

    ref = _sample_ref()
    # High-risk work item without 2 signoffs
    work_item_dict = {
        "status": "verifying",
        "evidence_digest": "d" * 64,
        "governance": {"high_risk": True, "approval_required": True, "operator_intent_ref": "intent-1"},
    }
    job = SyncJob(
        job_type=SyncJobType.OUTBOUND_STATUS,
        platform=ConnectorPlatform.GITHUB,
        ref=ref,
        payload={
            "target_status": "Closed",
            "work_item": work_item_dict,
            "signoffs": [],  # missing 2-person signoff
        },
    )
    worker.enqueue_job(job)
    batch = worker.sync_once()

    assert batch.held == 1
    assert batch.succeeded == 0
    assert job.status == SyncJobStatus.HELD_BY_GOVERNANCE
    assert len(mock_conn.transitions) == 0
    # Governance notice comment posted
    assert len(mock_conn.comments) == 1
    assert "Release Gate Notice" in mock_conn.comments[0][1]


def test_sync_worker_completes_outbound_close_when_dual_approval_present():
    registry = ConnectorRegistry()
    mock_conn = MockConnector()
    registry.register(mock_conn)
    worker = SyncWorker(registry=registry)

    ref = _sample_ref()
    work_item_dict = {
        "status": "verifying",
        "evidence_digest": "e" * 64,
        "governance": {"high_risk": True, "approval_required": True, "operator_intent_ref": "intent-1"},
    }
    signoffs = [
        {"actor": "builder-agent", "role": "verifier"},
        {"actor": "ops-lead", "role": "release_guardian"},
    ]
    job = SyncJob(
        job_type=SyncJobType.OUTBOUND_STATUS,
        platform=ConnectorPlatform.GITHUB,
        ref=ref,
        payload={
            "target_status": "Closed",
            "work_item": work_item_dict,
            "signoffs": signoffs,
        },
    )
    worker.enqueue_job(job)
    batch = worker.sync_once()

    assert batch.succeeded == 1
    assert job.status == SyncJobStatus.COMPLETED
    assert len(mock_conn.transitions) == 1
    assert mock_conn.transitions[0].target_status == "Closed"


def test_sync_worker_retries_on_transient_failure():
    registry = ConnectorRegistry()
    mock_conn = MockConnector()
    mock_conn.fail_next = True
    registry.register(mock_conn)
    worker = SyncWorker(registry=registry)

    ref = _sample_ref()
    job = SyncJob(
        job_type=SyncJobType.OUTBOUND_STATUS,
        platform=ConnectorPlatform.GITHUB,
        ref=ref,
        payload={"target_status": "In Progress"},
        max_retries=2,
    )
    worker.enqueue_job(job)

    # First attempt: fails, remains pending with retry_count 1
    batch1 = worker.sync_once()
    assert batch1.failed == 0  # not yet permanently failed
    assert job.status == SyncJobStatus.PENDING
    assert job.retry_count == 1

    # Second attempt: fails again, hits max_retries
    batch2 = worker.sync_once()
    assert batch2.failed == 1
    assert job.status == SyncJobStatus.FAILED
    assert job.retry_count == 2
