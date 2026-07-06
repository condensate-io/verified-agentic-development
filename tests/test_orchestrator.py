from vad.contracts.models import (
    AutonomyTier,
    Constraints,
    EIP,
    Goal,
    Invariants,
    ModelBudget,
    ProofObligation,
    ReleaseRequirements,
    RiskTier,
    TelemetryRequirements,
    ToolPermissions,
)
from vad.evidence.recorder import EvidenceRecorder
from vad.loop.orchestrator import VADOrchestrator
from vad.loop.state import LoopStatus
from vad.effort.scoring import DiffMetrics, MeesPenalties, score_effort
from vad.proof.plan import ProofMapping, ProofPlan, compute_eip_digest
from vad.release.flags import FeatureFlags
from vad.release.gates import ReleaseManager
from vad.router.models import TokenBudget
from vad.router.providers.fake import FakeProvider
from vad.cli import main
from unittest.mock import patch
import json
import pytest
import sys
import yaml


def create_eip(max_tokens=1000, max_cost=1.0):
    return EIP(
        version="1.0.0",
        name="orchestrator-test",
        goal=Goal(description="Run orchestrator", success_criteria=["verification passes"]),
        non_goals=[],
        risk_tier=RiskTier.LOW,
        autonomy_tier=AutonomyTier.ASSISTED,
        scope_boundaries=["tests"],
        invariants=Invariants(functional=["verification is guarded"]),
        constraints=Constraints(),
        proof_obligations=[ProofObligation(id="po-1", kind="unit", description="unit proof")],
        tool_permissions=ToolPermissions(allowed=["pytest"], denied=["network"]),
        memory_requirements=[],
        model_budget=ModelBudget(max_tokens=max_tokens, max_cost=max_cost, max_loop_depth=3),
        release_requirements=ReleaseRequirements(required=False, gates=[]),
        telemetry_requirements=TelemetryRequirements(required=False, signals=[]),
    )


def create_release_eip():
    eip = create_eip()
    return EIP(**{
        **eip.model_dump(),
        "release_requirements": ReleaseRequirements(required=True, strategy="gated", gates=["health"]),
        "telemetry_requirements": TelemetryRequirements(required=True, signals=["health"]),
    })


def create_plan(eip, command="pytest --version"):
    return ProofPlan(
        eip_version=eip.version,
        eip_digest=compute_eip_digest(eip),
        mappings=[ProofMapping(obligation_id="po-1", test_command=command)],
    )


def test_orchestrator_passes_when_verification_passes():
    eip = create_eip()
    result = VADOrchestrator().run(eip, create_plan(eip), builder="alice", verifier="bob")

    assert result.final_decision == LoopStatus.PASSED
    assert result.evidence["verification"]["passed"] is True
    assert result.evidence["eip"]["digest"]
    assert result.evidence["agents"]["builder"] == "alice"
    assert result.evidence["effort"]["effort_type"] == "feature"
    assert result.evidence["tokens"]["budget"] == eip.model_budget.max_tokens
    assert result.evidence["policy_decisions"]
    assert result.evidence["tool_calls"][0]["event"] == "verification_tool_call"
    assert result.evidence["model_routes"][0]["event"] == "model_budget"
    assert result.evidence["memory_events"][0]["event"] == "memory_requirements"
    assert result.evidence["release"]["event"] == "release_gate"


def test_orchestrator_fails_when_verification_fails():
    eip = create_eip()
    result = VADOrchestrator().run(
        eip,
        create_plan(eip, command="pytest does-not-exist.py"),
        builder="alice",
        verifier="bob",
    )

    assert result.final_decision == LoopStatus.FAILED
    assert result.evidence["verification"]["passed"] is False
    assert result.evidence["tool_calls"][0]["exit_code"] != 0


def test_orchestrator_rejects_builder_self_approval():
    eip = create_eip()
    result = VADOrchestrator().run(eip, create_plan(eip), builder="alice", verifier="alice")

    assert result.final_decision == LoopStatus.NEEDS_HUMAN
    assert result.evidence["policy_decisions"][0]["allow"] is False


def test_orchestrator_blocks_exhausted_budget():
    eip = create_eip(max_tokens=0)
    result = VADOrchestrator().run(eip, create_plan(eip), builder="alice", verifier="bob")

    assert result.final_decision == LoopStatus.BLOCKED
    assert result.evidence["blocker"] == "budget exhausted"
    assert result.evidence["verification"] is None
    assert result.evidence["model_routes"][0]["max_tokens"] == 0
    assert result.evidence["memory_events"]
    assert result.evidence["release"]["event"] == "release_gate"


def test_orchestrator_records_effort_score():
    eip = create_eip()
    effort_score = score_effort("feature", MeesPenalties(diff=3, spread=1))
    effort_metrics = DiffMetrics(changed_files=1, insertions=2, deletions=1, dependency_files_changed=0)

    result = VADOrchestrator(effort_score=effort_score, effort_metrics=effort_metrics).run(
        eip,
        create_plan(eip),
        builder="alice",
        verifier="bob",
    )

    assert result.final_decision == LoopStatus.PASSED
    assert result.evidence["effort"]["mees"] == 96
    assert result.evidence["effort"]["changed_files"] == 1
    assert result.evidence["effort"]["line_delta"] == 3


def test_orchestrator_blocks_mees_under_50():
    eip = create_eip()
    effort_score = score_effort("feature", MeesPenalties(diff=60))

    result = VADOrchestrator(effort_score=effort_score).run(eip, create_plan(eip), builder="alice", verifier="bob")

    assert result.final_decision == LoopStatus.BLOCKED
    assert result.evidence["blocker"] == "MEES under 50 requires human review"
    assert result.evidence["effort"]["mees"] == 40


def test_orchestrator_requires_justification_for_large_spread():
    eip = create_eip()
    effort_score = score_effort("feature", MeesPenalties(spread=11))
    effort_metrics = DiffMetrics(changed_files=11, insertions=1, deletions=1, dependency_files_changed=0)

    blocked = VADOrchestrator(effort_score=effort_score, effort_metrics=effort_metrics).run(
        eip,
        create_plan(eip),
        builder="alice",
        verifier="bob",
    )
    allowed = VADOrchestrator(
        effort_score=effort_score,
        effort_metrics=effort_metrics,
        effort_justification="shared contract update",
    ).run(eip, create_plan(eip), builder="alice", verifier="bob")

    assert blocked.final_decision == LoopStatus.BLOCKED
    assert blocked.evidence["blocker"] == "changed files over 10 require justification"
    assert allowed.final_decision == LoopStatus.PASSED


def test_orchestrator_requires_dependency_approval():
    eip = create_eip()
    effort_score = score_effort("feature", MeesPenalties(dependency=10))
    effort_metrics = DiffMetrics(changed_files=1, insertions=1, deletions=1, dependency_files_changed=1)

    blocked = VADOrchestrator(effort_score=effort_score, effort_metrics=effort_metrics).run(
        eip,
        create_plan(eip),
        builder="alice",
        verifier="bob",
    )
    approved = VADOrchestrator(
        effort_score=effort_score,
        effort_metrics=effort_metrics,
        dependency_approval=True,
    ).run(eip, create_plan(eip), builder="alice", verifier="bob")

    assert blocked.final_decision == LoopStatus.BLOCKED
    assert blocked.evidence["blocker"] == "new dependency requires explicit approval"
    assert approved.final_decision == LoopStatus.PASSED


def test_recorder_preserves_token_usage_when_available():
    eip = create_eip()
    plan = create_plan(eip)
    token_budget = TokenBudget.create(1000, estimated=250, optimization_notes=["compact output"]).record_usage(300)

    evidence = EvidenceRecorder().create_run_evidence(
        eip,
        plan,
        builder="alice",
        verifier="bob",
        final_decision="passed",
        policy_decisions=[],
        token_budget=token_budget,
    )

    assert evidence.tokens.budget == 1000
    assert evidence.tokens.estimated == 250
    assert evidence.tokens.used == 300
    assert evidence.tokens.remaining == 700
    assert evidence.tokens.optimization_notes == ["compact output"]


def test_recorder_preserves_route_events_when_available():
    eip = create_eip()
    plan = create_plan(eip)
    route_events = [{
        "event": "route_decision",
        "selected_model": "standard",
        "selected_tier": "tier1",
        "estimated_tokens": 1000,
        "estimated_cost": 0.01,
        "reason": "low-risk formatting uses cheapest allowed tier",
    }]

    evidence = EvidenceRecorder().create_run_evidence(
        eip,
        plan,
        builder="alice",
        verifier="bob",
        final_decision="passed",
        policy_decisions=[],
        model_routes=route_events,
    )

    assert evidence.model_routes == route_events


def test_orchestrator_provider_call_respects_risk_tier_and_records_budget():
    eip = create_eip()

    result = VADOrchestrator(provider=FakeProvider()).run(eip, create_plan(eip), builder="alice", verifier="bob")

    assert result.final_decision == LoopStatus.PASSED
    assert result.evidence["model_routes"][0]["event"] == "provider_completion"
    assert result.evidence["model_routes"][0]["provider_request_id"] == "fake-1"
    assert result.evidence["tokens"]["used"] > 0


def test_orchestrator_provider_call_blocks_insufficient_budget():
    eip = create_eip(max_cost=0.000001)

    result = VADOrchestrator(provider=FakeProvider()).run(eip, create_plan(eip), builder="alice", verifier="bob")

    assert result.final_decision == LoopStatus.BLOCKED
    assert result.evidence["blocker"] == "provider cost exceeds budget"
    assert result.evidence["model_routes"][0]["denial"] == "provider cost exceeds budget"


def test_orchestrator_provider_denial_is_captured_in_final_evidence():
    eip = create_eip()

    result = VADOrchestrator(
        provider=FakeProvider(delay_seconds=10),
        provider_model="fake-chat",
    ).run(eip, create_plan(eip), builder="alice", verifier="bob")

    assert result.final_decision == LoopStatus.BLOCKED
    assert result.evidence["blocker"] == "provider denied: provider timeout"
    assert result.evidence["model_routes"][0]["event"] == "provider_denied"
    assert result.evidence["model_routes"][0]["retryable"] is True


def test_orchestrator_passes_healthy_flagged_release():
    eip = create_release_eip()
    flags = FeatureFlags()
    flags.set_flag(eip.name, True)

    result = VADOrchestrator(
        release_manager=ReleaseManager(flags=flags),
        release_metrics={"health": 1.0},
    ).run(eip, create_plan(eip), builder="alice", verifier="bob")

    assert result.final_decision == LoopStatus.PASSED
    assert result.evidence["release"]["decision"] == "passed"
    assert LoopStatus.RELEASING in result.state.history


def test_orchestrator_blocks_flag_off_release():
    eip = create_release_eip()

    result = VADOrchestrator(
        release_manager=ReleaseManager(flags=FeatureFlags()),
        release_metrics={"health": 1.0},
    ).run(eip, create_plan(eip), builder="alice", verifier="bob")

    assert result.final_decision == LoopStatus.FAILED
    assert result.evidence["release"]["decision"] == "failed"
    assert "Feature flag is off" in result.evidence["release"]["error"]


def test_orchestrator_release_health_regression_fails():
    eip = create_release_eip()
    flags = FeatureFlags()
    flags.set_flag(eip.name, True)

    result = VADOrchestrator(
        release_manager=ReleaseManager(flags=flags),
        release_metrics={"health": 0.90},
    ).run(eip, create_plan(eip), builder="alice", verifier="bob")

    assert result.final_decision == LoopStatus.FAILED
    assert "Rollback triggered" in result.evidence["release"]["error"]


def write_loop_inputs(tmp_path, command="pytest --version"):
    eip = create_eip()
    plan = create_plan(eip, command=command)
    eip_file = tmp_path / "eip.yaml"
    proof_file = tmp_path / "proof.yaml"
    eip_file.write_text(yaml.safe_dump(eip.model_dump(mode="json"), sort_keys=False))
    proof_file.write_text(yaml.safe_dump(plan.model_dump(mode="json"), sort_keys=False))
    return eip_file, proof_file


def test_cli_loop_run_pass_path_creates_evidence(tmp_path):
    eip_file, proof_file = write_loop_inputs(tmp_path)
    evidence_file = tmp_path / "evidence.json"

    with patch.object(sys, "argv", ["vad", "loop", "run", str(eip_file), str(proof_file), "--out", str(evidence_file)]):
        main()

    evidence = json.loads(evidence_file.read_text())
    assert evidence["final_decision"] == "passed"
    assert evidence["evidence"]["verification"]["passed"] is True
    assert evidence["evidence"]["schema_version"] == "1.0.0"


def test_cli_loop_run_fail_path_creates_evidence_and_exits_nonzero(tmp_path):
    eip_file, proof_file = write_loop_inputs(tmp_path, command="pytest does-not-exist.py")
    evidence_file = tmp_path / "evidence.json"

    with patch.object(sys, "argv", ["vad", "loop", "run", str(eip_file), str(proof_file), "--out", str(evidence_file)]):
        with pytest.raises(SystemExit) as e:
            main()

    evidence = json.loads(evidence_file.read_text())
    assert e.value.code == 1
    assert evidence["final_decision"] == "failed"
