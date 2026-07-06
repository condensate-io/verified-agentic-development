from __future__ import annotations

import json
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from vad.deploy.attestation import sign_deployment_attestation, verify_deployment_attestation
from vad.deploy.models import DeploymentPlan, DeploymentTarget
from vad.deploy.providers.fake import FakeDeploymentProvider
from vad.evidence.bundle import AgentEvidence, EffortEvidence, EvidenceRef, RunEvidence, TokenEvidence, VerificationEvidence
from vad.feedback.analyzer import FeedbackAnalyzer
from vad.server.db.store import DashboardActivity, ServerStore
from vad.signing.local import LocalDevelopmentSigner


class TelemetryGateResult(BaseModel):
    name: str
    observed_health: float
    minimum_health: float
    passed: bool


class SignedDeploymentDemoResult(BaseModel):
    plan: dict
    dry_run: dict
    deployment: dict
    telemetry: list[TelemetryGateResult] = Field(default_factory=list)
    attestation: dict
    attestation_verified: bool
    final_decision: str


class FailedDeploymentDemoResult(BaseModel):
    deployment: dict
    telemetry: list[TelemetryGateResult] = Field(default_factory=list)
    rollback: dict
    feedback_proposals: list[dict] = Field(default_factory=list)
    run_evidence: dict
    dashboard: dict
    final_decision: str


def run_signed_deployment_demo(fixture: Path, out_dir: Path, *, key_id: str = "level3-demo-deploy") -> SignedDeploymentDemoResult:
    target = DeploymentTarget(**_load_structured(fixture / "deployment-target.yaml"))
    plan = DeploymentPlan(
        plan_id="level3-demo-staging",
        target=target,
        approval_ref="approval:level3-demo-release-guardian",
        evidence_ref="evidence:level3-demo-success",
    )
    provider = FakeDeploymentProvider()
    dry_run = provider.dry_run(plan)
    deployment = provider.apply(plan)
    telemetry = _evaluate_telemetry(target)

    signer = LocalDevelopmentSigner(key_id, b"level3-demo-local-secret")
    attestation = sign_deployment_attestation(deployment, signer)
    attestation_verified = verify_deployment_attestation(attestation, signer)
    final_decision = "passed" if deployment["status"] == "applied" and all(gate.passed for gate in telemetry) and attestation_verified else "blocked"

    result = SignedDeploymentDemoResult(
        plan=plan.model_dump(mode="json"),
        dry_run=dry_run,
        deployment=deployment,
        telemetry=telemetry,
        attestation=attestation.model_dump(mode="json"),
        attestation_verified=attestation_verified,
        final_decision=final_decision,
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "deployment-demo.json").write_text(json.dumps(result.model_dump(mode="json"), indent=2), encoding="utf-8")
    return result


def run_failed_deployment_demo(fixture: Path, out_dir: Path, *, db_path: Path | None = None) -> FailedDeploymentDemoResult:
    target = DeploymentTarget(**_load_structured(fixture / "deployment-target.yaml"))
    plan = DeploymentPlan(
        plan_id="level3-demo-failure",
        target=target,
        approval_ref="approval:level3-demo-release-guardian",
        evidence_ref="evidence:level3-demo-failure",
    )
    provider = FakeDeploymentProvider(targets={target.target_id: "f" * 64})
    deployment = provider.apply(plan)
    telemetry = _evaluate_failed_telemetry(target)
    rollback = provider.rollback(deployment["deployment_id"])
    failing_metric = telemetry[0].name if telemetry else "release_health"
    release_outcome = {
        "event": "release_gate",
        "decision": "failed",
        "error": f"Rollback triggered: '{failing_metric}' health ({telemetry[0].observed_health if telemetry else 0.0}) is below threshold ({telemetry[0].minimum_health if telemetry else 1.0}).",
    }
    proposals = FeedbackAnalyzer().propose_release_updates(release_outcome)
    evidence = _failure_run_evidence(fixture, release_outcome, rollback, proposals)

    out_dir.mkdir(parents=True, exist_ok=True)
    store = ServerStore(db_path or out_dir / "failure-dashboard.sqlite3")
    stored = store.save_run_evidence(evidence)
    store.save_dashboard_activity(DashboardActivity(
        activity_id="level3-demo-failure-rollout",
        run_id=evidence.run_id,
        kind="deployment",
        status="failed",
        client="Antigravity",
        actor="release-guardian",
        role="release_guardian",
        task_id="rollout",
        summary=release_outcome["error"],
        evidence_digest=stored.evidence_digest,
        details={"rollback_status": rollback["status"]},
    ))
    store.save_dashboard_activity(DashboardActivity(
        activity_id="level3-demo-failure-feedback",
        run_id=evidence.run_id,
        kind="work_item",
        status="blocked",
        client="Codex",
        actor="retro",
        role="auditor",
        task_id="feedback",
        summary=proposals[0].reason if proposals else "Release failure requires review.",
        evidence_digest=stored.evidence_digest,
    ))

    result = FailedDeploymentDemoResult(
        deployment=deployment,
        telemetry=telemetry,
        rollback=rollback,
        feedback_proposals=[proposal.model_dump(mode="json") for proposal in proposals],
        run_evidence=evidence.model_dump(mode="json"),
        dashboard=store.dashboard_snapshot(),
        final_decision="blocked",
    )
    (out_dir / "failure-demo.json").write_text(json.dumps(result.model_dump(mode="json"), indent=2), encoding="utf-8")
    return result


def _evaluate_telemetry(target: DeploymentTarget) -> list[TelemetryGateResult]:
    return [
        TelemetryGateResult(
            name=requirement.name,
            observed_health=1.0,
            minimum_health=requirement.minimum_health,
            passed=1.0 >= requirement.minimum_health,
        )
        for requirement in target.telemetry
    ]


def _evaluate_failed_telemetry(target: DeploymentTarget) -> list[TelemetryGateResult]:
    return [
        TelemetryGateResult(
            name=requirement.name,
            observed_health=max(0.0, requirement.minimum_health - 0.2),
            minimum_health=requirement.minimum_health,
            passed=False,
        )
        for requirement in target.telemetry
    ]


def _failure_run_evidence(fixture: Path, release_outcome: dict, rollback: dict, proposals: list) -> RunEvidence:
    return RunEvidence(
        run_id="level3-demo-failure",
        created_at="2026-07-01T00:20:00",
        eip=EvidenceRef(path=str(fixture / "eip.yaml"), digest="level3-demo-eip-digest"),
        proof_plan=EvidenceRef(path=str(fixture / "proof-plan.yaml"), digest="level3-demo-proof-plan-digest"),
        agents=AgentEvidence(builder="claude-code-builder", verifier="codex-verifier"),
        verification=VerificationEvidence(passed=False, results=[release_outcome]),
        release={
            "evaluated": True,
            "decision": "failed",
            "rollback": rollback,
        },
        feedback={
            "proposals": [proposal.model_dump(mode="json") for proposal in proposals],
        },
        effort=EffortEvidence(
            effort_type="feature",
            mees=72,
            policy="warn",
            changed_files=1,
            line_delta=0,
            new_dependencies=0,
            complexity_delta=0,
            maintainability_delta=0,
        ),
        tokens=TokenEvidence(
            budget=12000,
            estimated=600,
            used=480,
            remaining=11520,
            optimization_notes=["Reused fixture deployment target and fake provider state for rollback proof."],
        ),
        final_decision="blocked",
        blocker=release_outcome["error"],
    )


def _load_structured(path: Path) -> dict:
    with path.open(encoding="utf-8") as handle:
        if path.suffix in {".yaml", ".yml"}:
            return yaml.safe_load(handle)
        return json.load(handle)
