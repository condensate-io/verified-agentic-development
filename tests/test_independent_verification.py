from datetime import datetime, timedelta, timezone
import shutil
import subprocess
from pathlib import Path

import pytest
from pydantic import ValidationError

from vad.contracts.models import (
    AssuranceLevel,
    AutonomyTier,
    Constraints,
    EIP,
    FreshnessPolicy,
    Goal,
    Invariants,
    MemoryRequirement,
    ModelBudget,
    ProofKind,
    ProofObligation,
    ReleaseRequirements,
    RiskTier,
    TelemetryRequirements,
    ToolPermissions,
)
from vad.guards.execution import ExecutionGuard
from vad.policy.decisions import PolicyDecision
from vad.policy.engine import PolicyEngine
from vad.proof.plan import ProofMapping, ProofPlan, VerifyStatus, compute_eip_digest
from vad.verify.freshness import FreshnessValidator, compute_proof_plan_digest
from vad.verify.isolated_executor import (
    IsolatedExecutionConfig,
    IsolatedVerifierExecutor,
    compute_env_digest,
    compute_tree_hash,
)
from vad.verify.report import (
    BuilderClaim,
    IndependentVerificationResult,
    VerifierResult,
)
from vad.verify.runner import CostOrderedLadderRunner, VerifierRunner


class _LocalPolicyEngineStub(PolicyEngine):
    """Deterministic in-process policy evaluator used only when opa binary is unavailable."""

    def evaluate_decision(self, policy_path: str, input_data: dict) -> PolicyDecision:
        tool = input_data.get("tool")
        if input_data.get("action") == "use_tool" and tool not in {"shell", "network"}:
            return PolicyDecision(allow=True, reasons=["policy allow rule matched"])
        return PolicyDecision(allow=False, denials=["tool access denied"])


def _execution_guard_for_env() -> ExecutionGuard:
    guard = ExecutionGuard()
    if shutil.which("opa") is None:
        guard.engine = _LocalPolicyEngineStub()
    return guard


def _make_eip(
    *,
    risk_tier: RiskTier = RiskTier.MEDIUM,
    assurance_level: AssuranceLevel | None = None,
    freshness_policy: FreshnessPolicy | None = None,
    obligations: list[ProofObligation] | None = None,
) -> EIP:
    return EIP(
        version="1.0.0",
        name="independent-verify-sample",
        goal=Goal(description="Verify add logic", success_criteria=["All tests pass"]),
        non_goals=["Deploy"],
        risk_tier=risk_tier,
        autonomy_tier=AutonomyTier.BOUNDED,
        scope_boundaries=["app.py"],
        invariants=Invariants(functional=["add is commutative"]),
        constraints=Constraints(),
        proof_obligations=obligations
        or [ProofObligation(id="po-1", kind=ProofKind.UNIT, description="Unit tests pass")],
        tool_permissions=ToolPermissions(allowed=["pytest", "python"], denied=["network"]),
        memory_requirements=[MemoryRequirement(scope="project", purpose="verification")],
        model_budget=ModelBudget(max_tokens=1000, max_cost=1.0, max_loop_depth=3),
        release_requirements=ReleaseRequirements(required=False, gates=[]),
        telemetry_requirements=TelemetryRequirements(required=False, signals=[]),
        assurance_level=assurance_level,
        freshness_policy=freshness_policy,
    )


def _make_plan(eip: EIP, command: str = "pytest test_app.py") -> ProofPlan:
    return ProofPlan(
        eip_version=eip.version,
        eip_digest=compute_eip_digest(eip),
        mappings=[
            ProofMapping(obligation_id=ob.id, test_command=command)
            for ob in eip.proof_obligations
        ],
    )


def _write_sample_project(workspace: Path, *, passing: bool = True) -> None:
    (workspace / "app.py").write_text(
        "def add(a: int, b: int) -> int:\n"
        "    return a + b\n",
        encoding="utf-8",
    )
    assertion = "assert add(2, 3) == 5" if passing else "assert add(2, 3) == 999"
    (workspace / "test_app.py").write_text(
        "from app import add\n\n"
        "def test_add() -> None:\n"
        f"    {assertion}\n",
        encoding="utf-8",
    )


def test_builder_claim_is_non_authoritative_and_cannot_satisfy_verification(tmp_path):
    _write_sample_project(tmp_path, passing=False)
    eip = _make_eip(risk_tier=RiskTier.MEDIUM)
    plan = _make_plan(eip, command="python -m pytest test_app.py")

    claim = BuilderClaim(
        claim_id="claim-1",
        builder_actor="builder-agent",
        commit_sha="commit-123",
        tree_hash=compute_tree_hash(tmp_path),
        summary="I ran tests locally and they passed.",
        claimed_passed=True,
    )
    assert claim.authoritative is False

    with pytest.raises(ValidationError):
        BuilderClaim(
            claim_id="claim-2",
            builder_actor="builder-agent",
            commit_sha="commit-123",
            tree_hash="abc",
            summary="Fake authoritative claim",
            claimed_passed=True,
            authoritative=True,  # type: ignore[arg-type]
        )

    runner = VerifierRunner(
        eip,
        plan,
        execution_guard=_execution_guard_for_env(),
        cwd=str(tmp_path),
    )
    result = runner.run_independent(
        builder_actor="builder-agent",
        verifier_actor="verifier-agent",
        commit_sha="commit-123",
        builder_claim=claim,
    )

    assert result.passed is False
    assert result.assurance_achieved == AssuranceLevel.A1_BASIC
    assert result.obligation_results[0].status == VerifyStatus.FAIL


def test_actor_separation_fails_closed_on_result_and_executor(tmp_path):
    _write_sample_project(tmp_path, passing=True)
    eip = _make_eip(risk_tier=RiskTier.MEDIUM)
    plan = _make_plan(eip, command="python -m pytest test_app.py")
    now = datetime.now(timezone.utc)

    with pytest.raises(ValidationError, match="distinct from builder_actor"):
        IndependentVerificationResult(
            verification_id="ver-self",
            eip_digest=compute_eip_digest(eip),
            proof_plan_digest=compute_proof_plan_digest(plan),
            commit_sha="abc",
            tree_hash="tree-1",
            env_digest="env-1",
            builder_actor="same-agent",
            verifier_actor="same-agent",
            isolated_environment=True,
            assurance_achieved=AssuranceLevel.A2_INDEPENDENT,
            passed=True,
            obligation_results=[
                VerifierResult(obligation_id="po-1", status=VerifyStatus.PASS, output="ok")
            ],
            verified_at=now,
            expires_at=now + timedelta(hours=1),
        )

    executor = IsolatedVerifierExecutor(execution_guard=_execution_guard_for_env())
    with pytest.raises(PermissionError, match="distinct from builder_actor"):
        executor.verify_independent(
            eip,
            plan,
            builder_actor="builder-1",
            verifier_actor="builder-1",
            workspace_dir=tmp_path,
        )

    claim = BuilderClaim(
        claim_id="claim-self",
        builder_actor="builder-1",
        commit_sha="abc",
        tree_hash="tree",
        summary="self claim",
        claimed_passed=True,
    )
    with pytest.raises(PermissionError, match="builder_claim"):
        executor.verify_independent(
            eip,
            plan,
            builder_actor="other-builder",
            verifier_actor="builder-1",
            workspace_dir=tmp_path,
            builder_claim=claim,
        )


def test_freshness_validator_checks_all_drift_and_expiry_conditions():
    freshness_policy = FreshnessPolicy(
        max_evidence_age_seconds=300,
        require_head_commit_match=True,
        require_tree_hash_match=True,
        require_env_digest_match=True,
        invalidate_on_dependency_change=True,
    )
    eip = _make_eip(
        risk_tier=RiskTier.HIGH,
        assurance_level=AssuranceLevel.A3_ATTESTED,
        freshness_policy=freshness_policy,
    )
    plan = _make_plan(eip)
    verified_at = datetime(2026, 11, 24, 12, 0, 0, tzinfo=timezone.utc)
    result = IndependentVerificationResult(
        verification_id="ver-1",
        eip_digest=compute_eip_digest(eip),
        proof_plan_digest=compute_proof_plan_digest(plan),
        commit_sha="commit-aaa",
        tree_hash="tree-aaa",
        env_digest="env-aaa",
        builder_actor="builder-1",
        verifier_actor="verifier-1",
        isolated_environment=True,
        assurance_achieved=AssuranceLevel.A3_ATTESTED,
        passed=True,
        obligation_results=[
            VerifierResult(obligation_id="po-1", status=VerifyStatus.PASS, output="ok")
        ],
        verified_at=verified_at,
        expires_at=verified_at + timedelta(seconds=300),
    )

    validator = FreshnessValidator()

    fresh = validator.validate(
        result,
        eip=eip,
        proof_plan=plan,
        target_commit_sha="commit-aaa",
        target_tree_hash="tree-aaa",
        current_env_digest="env-aaa",
        dependency_changed=False,
        now=verified_at + timedelta(seconds=60),
    )
    assert fresh.is_fresh is True
    assert fresh.reasons == []
    assert fresh.age_seconds == 60.0

    # Expired TTL (beyond expires_at and max_evidence_age_seconds)
    expired = validator.validate(
        result,
        eip=eip,
        proof_plan=plan,
        target_commit_sha="commit-aaa",
        target_tree_hash="tree-aaa",
        current_env_digest="env-aaa",
        now=verified_at + timedelta(seconds=301),
    )
    assert expired.is_fresh is False
    assert any("expired" in r for r in expired.reasons)

    # Future-dated beyond 60s skew
    future = validator.validate(
        result,
        eip=eip,
        proof_plan=plan,
        target_commit_sha="commit-aaa",
        target_tree_hash="tree-aaa",
        current_env_digest="env-aaa",
        now=verified_at - timedelta(seconds=61),
    )
    assert future.is_fresh is False
    assert any("future-dated" in r for r in future.reasons)

    # EIP digest mismatch
    modified_eip = _make_eip(
        risk_tier=RiskTier.HIGH,
        assurance_level=AssuranceLevel.A3_ATTESTED,
        freshness_policy=freshness_policy,
        obligations=[
            ProofObligation(id="po-1", kind=ProofKind.SECURITY, description="Changed description")
        ],
    )
    eip_mismatch = validator.validate(
        result,
        eip=modified_eip,
        proof_plan=plan,
        target_commit_sha="commit-aaa",
        target_tree_hash="tree-aaa",
        current_env_digest="env-aaa",
        now=verified_at + timedelta(seconds=10),
    )
    assert eip_mismatch.is_fresh is False
    assert any("eip_digest" in r for r in eip_mismatch.reasons)

    # ProofPlan digest mismatch
    modified_plan = _make_plan(eip, command="pytest -k other")
    plan_mismatch = validator.validate(
        result,
        eip=eip,
        proof_plan=modified_plan,
        target_commit_sha="commit-aaa",
        target_tree_hash="tree-aaa",
        current_env_digest="env-aaa",
        now=verified_at + timedelta(seconds=10),
    )
    assert plan_mismatch.is_fresh is False
    assert any("proof_plan_digest" in r for r in plan_mismatch.reasons)

    # Commit SHA drift (rebase)
    commit_drift = validator.validate(
        result,
        eip=eip,
        proof_plan=plan,
        target_commit_sha="commit-bbb",
        target_tree_hash="tree-aaa",
        current_env_digest="env-aaa",
        now=verified_at + timedelta(seconds=10),
    )
    assert commit_drift.is_fresh is False
    assert any("commit_sha" in r for r in commit_drift.reasons)

    # Tree hash drift (post-verify edit)
    tree_drift = validator.validate(
        result,
        eip=eip,
        proof_plan=plan,
        target_commit_sha="commit-aaa",
        target_tree_hash="tree-bbb",
        current_env_digest="env-aaa",
        now=verified_at + timedelta(seconds=10),
    )
    assert tree_drift.is_fresh is False
    assert any("tree_hash" in r for r in tree_drift.reasons)

    # Env digest drift
    env_drift = validator.validate(
        result,
        eip=eip,
        proof_plan=plan,
        target_commit_sha="commit-aaa",
        target_tree_hash="tree-aaa",
        current_env_digest="env-bbb",
        now=verified_at + timedelta(seconds=10),
    )
    assert env_drift.is_fresh is False
    assert any("env_digest" in r for r in env_drift.reasons)

    # Dependency change invalidation
    dep_changed = validator.validate(
        result,
        eip=eip,
        proof_plan=plan,
        target_commit_sha="commit-aaa",
        target_tree_hash="tree-aaa",
        current_env_digest="env-aaa",
        dependency_changed=True,
        now=verified_at + timedelta(seconds=10),
    )
    assert dep_changed.is_fresh is False
    assert any("dependency" in r for r in dep_changed.reasons)

    # Unisolated evidence for A2+ EIP
    unisolated = result.model_copy(update={"isolated_environment": False})
    isolation_check = validator.validate(
        unisolated,
        eip=eip,
        proof_plan=plan,
        target_commit_sha="commit-aaa",
        target_tree_hash="tree-aaa",
        current_env_digest="env-aaa",
        now=verified_at + timedelta(seconds=10),
    )
    assert isolation_check.is_fresh is False
    assert any("isolation" in r for r in isolation_check.reasons)


def test_cost_ordered_ladder_fails_closed_on_exception_zero_tests_and_type_errors(tmp_path):
    # 1. Subprocess exception fails closed (never silent return True)
    def raising_runner(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="pytest", timeout=30)

    runner_exc = CostOrderedLadderRunner(cwd=str(tmp_path), subprocess_runner=raising_runner)
    receipt_exc = runner_exc.run_ladder(commit_sha="c1", mees_score=90)
    assert receipt_exc.passed is False
    assert receipt_exc.steps[2].step_name == "scoped_tests"
    assert receipt_exc.steps[2].status == "FAIL"
    assert "timed out" in (receipt_exc.steps[2].error or "").lower()

    # 2. Zero tests collected (exit code 5) fails in strict=True mode
    def no_tests_runner(*args, **kwargs):
        return subprocess.CompletedProcess(args=args[0], returncode=5, stdout="0 collected", stderr="")

    strict_runner = CostOrderedLadderRunner(
        cwd=str(tmp_path),
        strict=True,
        subprocess_runner=no_tests_runner,
    )
    receipt_strict = strict_runner.run_ladder(commit_sha="c2", mees_score=90)
    assert receipt_strict.passed is False
    assert receipt_strict.steps[2].step_name == "scoped_tests"
    assert receipt_strict.steps[2].status == "FAIL"
    assert "strict mode" in (receipt_strict.steps[2].error or "").lower()

    # 3. Non-strict preserves backward compatibility on empty scratch dir (exit code 5)
    non_strict_runner = CostOrderedLadderRunner(
        cwd=str(tmp_path),
        strict=False,
        subprocess_runner=no_tests_runner,
    )
    receipt_non_strict = non_strict_runner.run_ladder(commit_sha="c3", mees_score=90)
    assert receipt_non_strict.passed is True

    # 4. _run_types catches invalid string type annotation syntax
    bad_ann_dir = tmp_path / "bad_types"
    bad_ann_dir.mkdir()
    (bad_ann_dir / "mod.py").write_text(
        'def broken(x: "List[int") -> int:\n    return 1\n',
        encoding="utf-8",
    )
    type_runner = CostOrderedLadderRunner(cwd=str(bad_ann_dir), strict=True)
    ok, _, err = type_runner._run_types()
    assert ok is False
    assert "invalid string type annotation" in err


def test_verifier_runner_run_independent_end_to_end_and_docker_command(tmp_path):
    _write_sample_project(tmp_path, passing=True)
    eip = _make_eip(risk_tier=RiskTier.MEDIUM)
    plan = _make_plan(eip, command="python -m pytest test_app.py")

    runner = VerifierRunner(
        eip,
        plan,
        execution_guard=_execution_guard_for_env(),
        cwd=str(tmp_path),
    )

    now = datetime(2026, 11, 24, 12, 0, 0, tzinfo=timezone.utc)
    result = runner.run_independent(
        builder_actor="builder-codex",
        verifier_actor="verifier-isolated",
        commit_sha="deadbeef",
        now=now,
    )

    assert result.passed is True
    assert result.isolated_environment is True
    assert result.assurance_achieved == AssuranceLevel.A2_INDEPENDENT
    assert len(result.receipt_sha) == 64
    assert result.receipt_sha == result.compute_sha()
    assert result.tree_hash == compute_tree_hash(tmp_path)
    assert result.env_digest == compute_env_digest(IsolatedExecutionConfig(mode="sandbox"), tmp_path)

    validator = FreshnessValidator()
    fresh = validator.validate(
        result,
        eip=eip,
        proof_plan=plan,
        target_commit_sha="deadbeef",
        target_tree_hash=compute_tree_hash(tmp_path),
        current_env_digest=result.env_digest,
        now=now + timedelta(seconds=30),
    )
    assert fresh.is_fresh is True

    # Post-verification edit invalidates tree_hash
    (tmp_path / "app.py").write_text("def add(a: int, b: int) -> int:\n    return a - b\n", encoding="utf-8")
    stale_after_edit = validator.validate(
        result,
        eip=eip,
        proof_plan=plan,
        target_commit_sha="deadbeef",
        target_tree_hash=compute_tree_hash(tmp_path),
        current_env_digest=result.env_digest,
        now=now + timedelta(seconds=31),
    )
    assert stale_after_edit.is_fresh is False
    assert any("tree_hash" in r for r in stale_after_edit.reasons)

    # Attested + dual signoff upgrades assurance to A3/A4 when passing
    _write_sample_project(tmp_path, passing=True)
    result_a4 = runner.run_independent(
        builder_actor="builder-codex",
        verifier_actor="verifier-isolated",
        commit_sha="deadbeef",
        attested=True,
        dual_signoff=True,
        now=now,
    )
    assert result_a4.passed is True
    assert result_a4.assurance_achieved == AssuranceLevel.A4_RESTRICTED

    # Docker mode builds hardened container invocation and respects ExecutionGuard
    captured_cmds: list[list[str]] = []

    def fake_docker_runner(cmd, **kwargs):
        captured_cmds.append(cmd)
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="1 passed", stderr="")

    docker_executor = IsolatedVerifierExecutor(
        config=IsolatedExecutionConfig(mode="docker", docker_image="vad-runner:3.11"),
        execution_guard=_execution_guard_for_env(),
        subprocess_runner=fake_docker_runner,
    )
    docker_res = docker_executor.verify_independent(
        eip,
        plan,
        builder_actor="builder-1",
        verifier_actor="verifier-1",
        commit_sha="cafebabe",
        workspace_dir=tmp_path,
        attested=True,
    )
    assert docker_res.passed is True
    assert docker_res.assurance_achieved == AssuranceLevel.A3_ATTESTED
    assert len(captured_cmds) == 1
    assert captured_cmds[0][:5] == ["docker", "run", "--rm", "--network=none", "--read-only"]
    assert "vad-runner:3.11" in captured_cmds[0]

    # Forbidden command is blocked before docker invocation
    bad_plan = _make_plan(eip, command="rm -rf important")
    blocked_cmds: list[list[str]] = []
    docker_executor_blocked = IsolatedVerifierExecutor(
        config=IsolatedExecutionConfig(mode="docker"),
        execution_guard=_execution_guard_for_env(),
        subprocess_runner=lambda cmd, **kw: blocked_cmds.append(cmd)  # type: ignore[arg-type]
        or subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr=""),
    )
    blocked_res = docker_executor_blocked.verify_independent(
        eip,
        bad_plan,
        builder_actor="builder-1",
        verifier_actor="verifier-1",
        workspace_dir=tmp_path,
    )
    assert blocked_res.passed is False
    assert blocked_cmds == []
    assert "executable not allowed" in (blocked_res.obligation_results[0].error or "")


def test_unmapped_obligation_fails_closed(tmp_path):
    _write_sample_project(tmp_path, passing=True)
    eip = _make_eip(
        obligations=[
            ProofObligation(id="po-1", kind=ProofKind.UNIT, description="Unit"),
            ProofObligation(id="po-2", kind=ProofKind.SECURITY, description="Unmapped security"),
        ]
    )
    plan = ProofPlan(
        eip_version=eip.version,
        eip_digest=compute_eip_digest(eip),
        mappings=[ProofMapping(obligation_id="po-1", test_command="python -m pytest test_app.py")],
    )
    runner = VerifierRunner(
        eip,
        plan,
        execution_guard=_execution_guard_for_env(),
        cwd=str(tmp_path),
    )
    result = runner.run_independent(
        builder_actor="builder-1",
        verifier_actor="verifier-1",
        commit_sha="head-1",
    )
    assert result.passed is False
    assert result.assurance_achieved == AssuranceLevel.A1_BASIC
    assert any(r.status == VerifyStatus.UNMAPPED and r.obligation_id == "po-2" for r in result.obligation_results)
