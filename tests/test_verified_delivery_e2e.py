from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import subprocess
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest

from vad.audit.metrics import AutonomyMetricsCalculator
from vad.contracts.models import RiskTier
from vad.control_plane.checkpoints import CheckpointStage
from vad.control_plane.work_items import WorkItemStatus
from vad.feedback.lesson_review import LessonReviewService
from vad.memory.config import (
    MemoryProviderConfig,
    MemoryProviderKind,
    MemoryTransport,
)
from vad.memory.contracts import LessonReviewStatus, ProposedRuleType
from vad.memory.gateway import MemoryGateway
from vad.memory.providers.condensate_core import CondensateCoreProvider
from vad.memory.providers.local import LocalMemoryProvider
from vad.memory.providers.null import NullMemoryProvider
from vad.policy.autonomy_matrix import PolicyStage
from vad.release.decision import ReleaseOutcome
from vad.server.db.store import ServerStore
from vad.signing.ed25519_local import LocalEd25519KeyProvider
from vad.workflows import (
    VerifiedDeliveryRequest,
    VerifiedDeliveryResult,
    VerifiedDeliveryWorkflow,
)

_SEED_BYTES = bytes.fromhex(
    "9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60"
)


def _passing_executor(cmd_parts: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        args=cmd_parts,
        returncode=0,
        stdout=f"ok: {' '.join(cmd_parts)}\n",
        stderr="",
    )


def _make_workspace(tmp_path: Path, *, with_pytest_file: bool = False) -> Path:
    ws = tmp_path / "workspace"
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "app.py").write_text(
        "def add(a: int, b: int) -> int:\n    return a + b\n",
        encoding="utf-8",
    )
    if with_pytest_file:
        (ws / "test_app.py").write_text(
            "from app import add\n\ndef test_add() -> None:\n    assert add(2, 3) == 5\n",
            encoding="utf-8",
        )
    return ws


class _MockCondensateBackend:
    """Deterministic in-memory HTTP backend on http://127.0.0.1:8000 for E2E tests."""

    def __init__(self) -> None:
        self.facts: dict[str, dict[str, Any]] = {}
        self.lessons: dict[str, dict[str, Any]] = {}
        self.calls: list[tuple[str, str]] = []

    def http_handler(
        self,
        method: str,
        url: str,
        payload: dict[str, Any] | None,
        headers: dict[str, str],
    ) -> dict[str, Any]:
        self.calls.append((method, url))
        parsed = urlparse(url)
        path = parsed.path
        qs = parse_qs(parsed.query)

        if method == "GET" and path.endswith("/health"):
            return {"healthy": True, "detail": "mock http://127.0.0.1:8000 healthy"}
        if method == "POST" and path.endswith("/facts"):
            assert payload is not None
            fact = payload["fact"]
            self.facts[fact["fact_id"]] = fact
            return {"fact_id": fact["fact_id"]}
        if method == "GET" and path.endswith("/facts"):
            limit = int(qs.get("limit", ["50"])[0])
            return {"facts": list(self.facts.values())[:limit]}
        if method == "POST" and path.endswith("/lessons"):
            assert payload is not None
            lesson = payload["lesson"]
            self.lessons[lesson["lesson_id"]] = lesson
            return {"lesson_id": lesson["lesson_id"]}
        if method == "POST" and path.endswith("/status"):
            assert payload is not None
            lesson_id = payload["lesson_id"]
            if lesson_id not in self.lessons:
                raise KeyError(lesson_id)
            updated = dict(self.lessons[lesson_id])
            updated["review_status"] = payload["status"]
            updated["reviewed_by"] = payload["reviewed_by"]
            updated["reviewed_at"] = datetime.now(timezone.utc).isoformat()
            if payload.get("promotion_record_id") is not None:
                updated["promotion_record_id"] = payload["promotion_record_id"]
            self.lessons[lesson_id] = updated
            return {"lesson": updated}
        if method == "POST" and path.endswith("/query"):
            assert payload is not None
            query = payload["query"]
            q_text = query.get("query_text", "").strip().lower()
            q_tags = {t.lower() for t in query.get("domain_tags", [])}
            facts = [
                f
                for f in self.facts.values()
                if (not q_text or q_text in f["summary"].lower())
                or (q_tags and bool(q_tags & {t.lower() for t in f.get("domain_tags", [])}))
            ]
            lessons = [
                lesson
                for lesson in self.lessons.values()
                if (not q_text or q_text in lesson["title"].lower() or q_text in lesson["recommendation"].lower())
                or (q_tags and bool(q_tags & {t.lower() for t in lesson.get("domain_tags", [])}))
            ]
            return {"facts": facts, "lessons": lessons}
        raise RuntimeError(f"Unhandled route: {method} {url}")


def test_verified_delivery_all_four_risk_tiers_and_drift_e2e(tmp_path: Path) -> None:
    db_path = tmp_path / "risk_tiers.sqlite3"
    store = ServerStore(db_path)
    workspace = _make_workspace(tmp_path, with_pytest_file=True)
    key_provider = LocalEd25519KeyProvider.from_seed(
        _SEED_BYTES,
        key_id="ed25519-e2e-test",
        key_dir=tmp_path / ".vad" / "keys",
    )

    workflow = VerifiedDeliveryWorkflow(
        store=store,
        memory_gateway=MemoryGateway(provider=NullMemoryProvider()),
        key_provider=key_provider,
        workspace_dir=workspace,
        command_executor=_passing_executor,
    )

    # 1. LOW risk -> AUTONOMOUS_RELEASE
    low_res = workflow.execute(
        VerifiedDeliveryRequest(
            run_id="run-low-1",
            change_request="Add utility helper in app module so that formatting is clean.",
            eip_name="low-helper",
            risk_tier=RiskTier.LOW,
            domain_tags=["utils"],
            cost_usd=0.02,
        )
    )
    assert isinstance(low_res, VerifiedDeliveryResult)
    assert low_res.verification_passed is True
    assert low_res.freshness_passed is True
    assert low_res.release_decision.outcome == ReleaseOutcome.AUTONOMOUS_RELEASE
    assert low_res.release_decision.can_release is True
    assert low_res.audit_report.verified is True
    assert low_res.checkpoints_recorded == [stage.value for stage in CheckpointStage]
    assert store.load_work_item("wi-run-low-1").status == WorkItemStatus.COMPLETED

    # 2. MEDIUM risk with default ExecutionGuard (command_executor=None) -> AUTONOMOUS_RELEASE
    real_guard_workflow = VerifiedDeliveryWorkflow(
        store=store,
        memory_gateway=MemoryGateway(provider=NullMemoryProvider()),
        key_provider=key_provider,
        workspace_dir=workspace,
        command_executor=None,
    )
    med_res = real_guard_workflow.execute(
        VerifiedDeliveryRequest(
            run_id="run-med-1",
            change_request="Update adder logic in app module so that sums match spec.",
            eip_name="med-adder",
            risk_tier=RiskTier.MEDIUM,
            domain_tags=["math"],
            cost_usd=0.04,
        )
    )
    assert med_res.verification_passed is True
    assert med_res.freshness_passed is True
    assert med_res.release_decision.outcome == ReleaseOutcome.AUTONOMOUS_RELEASE
    assert med_res.release_decision.can_release is True
    assert med_res.audit_report.verified is True
    assert store.load_work_item("wi-run-med-1").status == WorkItemStatus.COMPLETED

    # 3a. HIGH risk without human approver -> HUMAN_APPROVAL_REQUIRED
    high_blocked = workflow.execute(
        VerifiedDeliveryRequest(
            run_id="run-high-blocked",
            change_request="Update auth session validation in auth module so that tokens expire.",
            eip_name="high-auth-blocked",
            risk_tier=RiskTier.HIGH,
            domain_tags=["auth"],
            human_approvers=[],
            stage=PolicyStage.DEPLOY,
            cost_usd=0.06,
        )
    )
    assert high_blocked.verification_passed is True
    assert high_blocked.freshness_passed is True
    assert high_blocked.release_decision.outcome == ReleaseOutcome.HUMAN_APPROVAL_REQUIRED
    assert high_blocked.release_decision.can_release is False
    assert high_blocked.audit_report.verified is True
    assert store.load_work_item("wi-run-high-blocked").status == WorkItemStatus.WAITING_FOR_HUMAN

    # 3b. HIGH risk with distinct human release guardian -> APPROVED_WITH_HUMAN
    high_approved = workflow.execute(
        VerifiedDeliveryRequest(
            run_id="run-high-approved",
            change_request="Update auth session validation in auth module so that tokens expire.",
            eip_name="high-auth-approved",
            risk_tier=RiskTier.HIGH,
            domain_tags=["auth"],
            human_approvers=["guardian-alice"],
            stage=PolicyStage.DEPLOY,
            cost_usd=0.08,
            human_review_seconds=45.0,
        )
    )
    assert high_approved.verification_passed is True
    assert high_approved.freshness_passed is True
    assert high_approved.release_decision.outcome == ReleaseOutcome.APPROVED_WITH_HUMAN
    assert high_approved.release_decision.can_release is True
    assert high_approved.release_decision.recorded_approvers == ["guardian-alice"]
    assert high_approved.audit_report.verified is True
    assert store.load_work_item("wi-run-high-approved").status == WorkItemStatus.COMPLETED

    # 4a. CRITICAL risk without explicit authorization + 2 distinct guardians -> RESTRICTED_AUTHORIZATION_REQUIRED
    crit_blocked = workflow.execute(
        VerifiedDeliveryRequest(
            run_id="run-crit-blocked",
            change_request="Rotate root signing key in auth module so that compromise is contained.",
            eip_name="crit-key-blocked",
            risk_tier=RiskTier.CRITICAL,
            domain_tags=["auth", "security"],
            human_approvers=["guardian-alice"],
            explicit_authorization_ref=None,
            stage=PolicyStage.DEPLOY,
            cost_usd=0.10,
        )
    )
    assert crit_blocked.verification_passed is True
    assert crit_blocked.freshness_passed is True
    assert crit_blocked.release_decision.outcome == ReleaseOutcome.RESTRICTED_AUTHORIZATION_REQUIRED
    assert crit_blocked.release_decision.can_release is False
    assert crit_blocked.audit_report.verified is True
    assert store.load_work_item("wi-run-crit-blocked").status == WorkItemStatus.WAITING_FOR_HUMAN

    # 4b. CRITICAL risk with explicit authorization + 2 distinct guardians -> APPROVED_RESTRICTED
    crit_approved = workflow.execute(
        VerifiedDeliveryRequest(
            run_id="run-crit-approved",
            change_request="Rotate root signing key in auth module so that compromise is contained.",
            eip_name="crit-key-approved",
            risk_tier=RiskTier.CRITICAL,
            domain_tags=["auth", "security"],
            human_approvers=["guardian-alice", "guardian-bob"],
            explicit_authorization_ref="CAB-2026-0042",
            stage=PolicyStage.DEPLOY,
            cost_usd=0.12,
            human_review_seconds=135.0,
        )
    )
    assert crit_approved.verification_passed is True
    assert crit_approved.freshness_passed is True
    assert crit_approved.release_decision.outcome == ReleaseOutcome.APPROVED_RESTRICTED
    assert crit_approved.release_decision.can_release is True
    assert crit_approved.release_decision.recorded_approvers == ["guardian-alice", "guardian-bob"]
    assert crit_approved.release_decision.explicit_authorization_ref == "CAB-2026-0042"
    assert crit_approved.audit_report.verified is True
    assert store.load_work_item("wi-run-crit-approved").status == WorkItemStatus.COMPLETED

    # 5. Post-verify drift fails closed via FreshnessValidator -> BLOCKED_STALE_EVIDENCE
    drift_res = workflow.execute(
        VerifiedDeliveryRequest(
            run_id="run-drift-1",
            change_request="Update API endpoint in app module so that schema stays stable.",
            eip_name="med-drift",
            risk_tier=RiskTier.MEDIUM,
            domain_tags=["api"],
            cost_usd=0.03,
        ),
        simulate_drift_after_verify=True,
    )
    assert drift_res.verification_passed is True
    assert drift_res.freshness_passed is False
    assert drift_res.release_decision.outcome == ReleaseOutcome.BLOCKED_STALE_EVIDENCE
    assert drift_res.release_decision.can_release is False
    assert store.load_work_item("wi-run-drift-1").status == WorkItemStatus.BLOCKED

    # 6. Verify AutonomyMetricsCalculator across all runs
    metrics = AutonomyMetricsCalculator().compute_from_sqlite(db_path)
    assert metrics.total_changes == 7
    assert metrics.accepted_changes == 4  # low, med, high_approved, crit_approved
    assert metrics.autonomous_accepted_changes == 2  # low, med
    assert metrics.autonomous_safe_pass_through_rate == pytest.approx(2 / 7)
    assert metrics.human_review_count == 2  # high_approved (45s), crit_approved (135s)
    assert metrics.human_review_time_median_seconds == pytest.approx(90.0)
    assert metrics.total_cost_usd == pytest.approx(0.02 + 0.04 + 0.06 + 0.08 + 0.10 + 0.12 + 0.03)
    assert metrics.cost_per_accepted_change_usd == pytest.approx(metrics.total_cost_usd / 4)


def test_closed_learning_loop_run1_to_human_approval_to_run2(tmp_path: Path) -> None:
    db_path = tmp_path / "learning_loop.sqlite3"
    store = ServerStore(db_path)
    workspace = _make_workspace(tmp_path)
    local_provider = LocalMemoryProvider(local_path=tmp_path / "memory.json")
    gateway = MemoryGateway(provider=local_provider)

    executed_commands: list[list[str]] = []

    def tracking_executor(cmd_parts: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        executed_commands.append(list(cmd_parts))
        return subprocess.CompletedProcess(args=cmd_parts, returncode=0, stdout="passed\n", stderr="")

    workflow = VerifiedDeliveryWorkflow(
        store=store,
        memory_gateway=gateway,
        workspace_dir=workspace,
        command_executor=tracking_executor,
    )

    # Run 1: produces ObservedFact + PROPOSED InferredLesson
    run1 = workflow.execute(
        VerifiedDeliveryRequest(
            run_id="run-learn-1",
            change_request="Fix login session handling in auth module so that sessions expire.",
            eip_name="auth-session-1",
            risk_tier=RiskTier.HIGH,
            domain_tags=["auth"],
            human_approvers=["guardian-alice"],
        ),
        candidate_lesson_title="Require auth token expiry security check",
        candidate_lesson_recommendation="Verify auth token expiry rejection in security proof suite",
        candidate_rule_type=ProposedRuleType.PROOF_OBLIGATION,
    )
    assert run1.applied_lesson_ids == []
    assert run1.published_fact_id is not None
    assert run1.published_lesson_id is not None
    assert run1.audit_report.verified is True

    promo = store.load_lesson_promotion(run1.published_lesson_id)
    assert promo is not None
    assert promo.review_status == LessonReviewStatus.PROPOSED.value

    run1_ev = store.load_run_evidence("run-learn-1").evidence
    assert run1_ev.verification is not None
    run1_obligation_ids = {r["obligation_id"] for r in run1_ev.verification.results}
    assert not any(ob.startswith("po-lesson-") for ob in run1_obligation_ids)

    # Run 1b: before human approval, unapproved lesson MUST be ignored
    run1b = workflow.execute(
        VerifiedDeliveryRequest(
            run_id="run-learn-1b",
            change_request="Update token refresh in auth module so that expired tokens fail.",
            eip_name="auth-refresh-1b",
            risk_tier=RiskTier.HIGH,
            domain_tags=["auth"],
            human_approvers=["guardian-alice"],
        )
    )
    assert run1b.applied_lesson_ids == []
    assert run1.published_fact_id in run1b.retrieved_fact_ids
    run1b_ev = store.load_run_evidence("run-learn-1b").evidence
    assert run1b_ev.verification is not None
    assert not any(
        r["obligation_id"].startswith("po-lesson-")
        for r in run1b_ev.verification.results
    )

    # Operator approves lesson via LessonReviewService
    review_service = LessonReviewService(store, memory_gateway=gateway)
    approved_record = review_service.approve_lesson(
        run1.published_lesson_id,
        reviewer="release-guardian-bob",
        reviewer_role="release_guardian",
        note="Mandatory regression proof for auth token expiry",
    )
    assert approved_record.review_status == LessonReviewStatus.APPROVED.value

    # Run 2: automatically retrieves approved lesson and tightens ProofPlan
    executed_commands.clear()
    run2 = workflow.execute(
        VerifiedDeliveryRequest(
            run_id="run-learn-2",
            change_request="Update token validation in auth module so that expired tokens fail.",
            eip_name="auth-validation-2",
            risk_tier=RiskTier.HIGH,
            domain_tags=["auth"],
            human_approvers=["guardian-alice"],
        )
    )
    assert run1.published_lesson_id in run2.applied_lesson_ids
    assert run1.published_fact_id in run2.retrieved_fact_ids
    assert run2.audit_report.verified is True

    run2_ev = store.load_run_evidence("run-learn-2").evidence
    assert run2_ev.verification is not None
    run2_obligation_ids = {r["obligation_id"] for r in run2_ev.verification.results}
    assert any(ob.startswith("po-lesson-") for ob in run2_obligation_ids)
    assert len(run2_obligation_ids) > len(run1_obligation_ids)
    assert ["pytest", "-m", "security"] in executed_commands


@pytest.mark.parametrize("provider_kind", ["null", "local", "condensate_http", "condensate_offline"])
def test_memory_agnostic_workflow_across_providers(tmp_path: Path, provider_kind: str) -> None:
    store = ServerStore(tmp_path / f"mem_{provider_kind}.sqlite3")
    workspace = _make_workspace(tmp_path)

    if provider_kind == "null":
        gateway = MemoryGateway(provider=NullMemoryProvider())
    elif provider_kind == "local":
        gateway = MemoryGateway(provider=LocalMemoryProvider())
    elif provider_kind == "condensate_http":
        backend = _MockCondensateBackend()
        cfg = MemoryProviderConfig(
            provider=MemoryProviderKind.CONDENSATE_CORE,
            transport=MemoryTransport.HTTP,
            endpoint_url="http://127.0.0.1:8000",
            project_id="vad-e2e",
        )
        provider = CondensateCoreProvider(config=cfg, http_transport=backend.http_handler)
        gateway = MemoryGateway(provider=provider)
    else:
        def failing_http(*args: Any, **kwargs: Any) -> dict[str, Any]:
            raise ConnectionError("http://127.0.0.1:8000 unreachable")

        cfg = MemoryProviderConfig(
            provider=MemoryProviderKind.CONDENSATE_CORE,
            transport=MemoryTransport.HTTP,
            endpoint_url="http://127.0.0.1:8000",
            fail_open_on_unavailable=True,
        )
        provider = CondensateCoreProvider(config=cfg, http_transport=failing_http)
        gateway = MemoryGateway(provider=provider, fail_open=True)

    workflow = VerifiedDeliveryWorkflow(
        store=store,
        memory_gateway=gateway,
        workspace_dir=workspace,
        command_executor=_passing_executor,
    )

    res1 = workflow.execute(
        VerifiedDeliveryRequest(
            run_id=f"run-{provider_kind}-1",
            change_request="Update token handler in auth module so that expiry is enforced.",
            eip_name=f"eip-{provider_kind}-1",
            risk_tier=RiskTier.MEDIUM,
            domain_tags=["auth"],
        ),
        candidate_lesson_title="Enforce token expiry check",
        candidate_lesson_recommendation="Verify auth token expiry check in security suite",
    )
    assert res1.verification_passed is True
    assert res1.freshness_passed is True
    assert res1.release_decision.outcome == ReleaseOutcome.AUTONOMOUS_RELEASE
    assert res1.release_decision.can_release is True
    assert res1.audit_report.verified is True
    assert res1.memory_degraded is (provider_kind == "condensate_offline")

    if provider_kind in {"local", "condensate_http"}:
        assert res1.published_lesson_id is not None
        LessonReviewService(store, memory_gateway=gateway).approve_lesson(
            res1.published_lesson_id,
            reviewer="guardian-alice",
            reviewer_role="release_guardian",
        )
        res2 = workflow.execute(
            VerifiedDeliveryRequest(
                run_id=f"run-{provider_kind}-2",
                change_request="Update token parser in auth module so that expiry is enforced.",
                eip_name=f"eip-{provider_kind}-2",
                risk_tier=RiskTier.MEDIUM,
                domain_tags=["auth"],
            )
        )
        assert res1.published_lesson_id in res2.applied_lesson_ids
        assert res2.audit_report.verified is True
