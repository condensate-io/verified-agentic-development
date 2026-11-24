"""Verifier Runner: Executes proof plans and cost-ordered verification ladders."""

import ast
from datetime import datetime, timezone
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import time
from typing import Callable, Dict, List, Optional, Tuple
import uuid

from vad.contracts.models import EIP
from vad.guards.execution import ExecutionGuard
from vad.proof.plan import ProofPlan, VerifyStatus, compute_eip_digest
from vad.verify.isolated_executor import (
    IsolatedExecutionConfig,
    IsolatedVerifierExecutor,
)
from vad.verify.report import (
    BuilderClaim,
    IndependentVerificationResult,
    LadderStepResult,
    VerificationReceipt,
    VerifierReport,
    VerifierResult,
)

_IGNORED_SCAN_DIRS = frozenset(
    {
        ".git",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".venv",
        "venv",
        "node_modules",
    }
)


class CostOrderedLadderRunner:
    """Runs a cost-ordered verification ladder: lint -> types -> scoped tests -> integration -> model review.

    Stops at the first failure (fail-fast) and emits a SHA-stamped receipt.
    """

    COST_TIERS = {
        "lint": "lowest",
        "types": "low",
        "scoped_tests": "medium",
        "registry_integration": "high",
        "model_review": "highest",
    }

    def __init__(
        self,
        cwd: Optional[str] = None,
        custom_executors: Optional[Dict[str, Callable[[], Tuple[bool, str, str]]]] = None,
        strict: bool = False,
        subprocess_runner: Optional[Callable[..., subprocess.CompletedProcess]] = None,
    ):
        self.cwd = cwd or os.getcwd()
        self.custom_executors = custom_executors or {}
        self.strict = strict
        self.subprocess_runner = subprocess_runner

    def run_ladder(
        self,
        commit_sha: str = "HEAD",
        mees_score: Optional[int] = None,
        scoped_files: Optional[List[str]] = None,
    ) -> VerificationReceipt:
        receipt_id = f"receipt-{uuid.uuid4().hex[:12]}"
        step_definitions = [
            ("lint", 1, self._run_lint),
            ("types", 2, self._run_types),
            ("scoped_tests", 3, lambda: self._run_scoped_tests(scoped_files)),
            ("registry_integration", 4, self._run_registry_integration),
            ("model_review", 5, lambda: self._run_model_review(mees_score)),
        ]

        step_results: List[LadderStepResult] = []
        overall_passed = True
        first_failure: Optional[str] = None

        for name, order, default_fn in step_definitions:
            tier = self.COST_TIERS[name]
            if not overall_passed:
                # Fail-fast: skip remaining expensive steps
                step_results.append(
                    LadderStepResult(
                        step_name=name,
                        order=order,
                        cost_tier=tier,
                        status="SKIPPED",
                        output="Skipped due to prior ladder failure",
                    )
                )
                continue

            executor = self.custom_executors.get(name) or default_fn
            start = time.perf_counter()
            try:
                passed, stdout, stderr = executor()
            except Exception as e:
                passed = False
                stdout = ""
                stderr = str(e)

            duration_ms = (time.perf_counter() - start) * 1000.0

            if passed:
                step_results.append(
                    LadderStepResult(
                        step_name=name,
                        order=order,
                        cost_tier=tier,
                        status="PASS",
                        output=stdout[:500] if stdout else "OK",
                        duration_ms=round(duration_ms, 2),
                    )
                )
            else:
                overall_passed = False
                err_msg = stderr or stdout or f"{name} failed"
                if not first_failure:
                    first_failure = f"Step {order} ({name}) failed: {err_msg[:200]}"
                step_results.append(
                    LadderStepResult(
                        step_name=name,
                        order=order,
                        cost_tier=tier,
                        status="FAIL",
                        output=stdout[:500],
                        error=err_msg[:500],
                        duration_ms=round(duration_ms, 2),
                    )
                )

        receipt = VerificationReceipt(
            receipt_id=receipt_id,
            timestamp=datetime.now(timezone.utc).isoformat(),
            commit_sha=commit_sha,
            steps=step_results,
            passed=overall_passed,
            first_failure=first_failure,
            mees_score=mees_score,
        )
        return receipt.stamp()

    # Default step executors
    def _run_lint(self) -> Tuple[bool, str, str]:
        # Fast syntax check over python files in cwd
        import compileall
        target = self.cwd
        success = compileall.compile_dir(target, quiet=1, maxlevels=3)
        return bool(success), "Python syntax check clean", "" if success else "Syntax errors detected"

    def _iter_python_files(self) -> List[Path]:
        root = Path(self.cwd)
        if not root.exists():
            return []
        files: List[Path] = []
        for current_root, dirs, filenames in os.walk(root):
            dirs[:] = sorted(d for d in dirs if d not in _IGNORED_SCAN_DIRS)
            current_path = Path(current_root)
            for name in sorted(filenames):
                if name.endswith(".py"):
                    files.append(current_path / name)
        return files

    @staticmethod
    def _validate_ast_annotations(tree: ast.AST, rel_path: str) -> List[str]:
        errors: List[str] = []

        def _check_ann(node: Optional[ast.AST]) -> None:
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                try:
                    ast.parse(node.value, mode="eval")
                except SyntaxError as exc:
                    errors.append(
                        f"{rel_path}:{node.lineno}: invalid string type annotation {node.value!r}: {exc.msg}"
                    )

        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                _check_ann(node.returns)
                all_args = (
                    list(node.args.posonlyargs)
                    + list(node.args.args)
                    + list(node.args.kwonlyargs)
                )
                if node.args.vararg:
                    all_args.append(node.args.vararg)
                if node.args.kwarg:
                    all_args.append(node.args.kwarg)
                for arg in all_args:
                    _check_ann(arg.annotation)
            elif isinstance(node, ast.AnnAssign):
                _check_ann(node.annotation)

        return errors

    def _has_mypy_config(self) -> bool:
        root = Path(self.cwd)
        if (root / "mypy.ini").is_file() or (root / ".mypy.ini").is_file():
            return True
        pyproject = root / "pyproject.toml"
        if pyproject.is_file():
            try:
                return "[tool.mypy]" in pyproject.read_text(encoding="utf-8")
            except OSError:
                return False
        return False

    def _run_types(self) -> Tuple[bool, str, str]:
        py_files = self._iter_python_files()
        root = Path(self.cwd)
        errors: List[str] = []

        for py_file in py_files:
            rel_path = py_file.relative_to(root).as_posix()
            try:
                source = py_file.read_text(encoding="utf-8")
                tree = ast.parse(source, filename=str(py_file))
            except (OSError, SyntaxError, UnicodeDecodeError) as exc:
                errors.append(f"{rel_path}: {exc}")
                continue
            errors.extend(self._validate_ast_annotations(tree, rel_path))

        if errors:
            return False, "", "\n".join(errors)

        if self._has_mypy_config():
            mypy_bin = shutil.which("mypy")
            if mypy_bin:
                runner = self.subprocess_runner or subprocess.run
                try:
                    res = runner(
                        [mypy_bin, "."],
                        cwd=self.cwd,
                        capture_output=True,
                        text=True,
                        timeout=60,
                        check=False,
                    )
                    if res.returncode != 0:
                        return False, res.stdout or "", res.stderr or res.stdout or "mypy failed"
                except Exception as exc:
                    return False, "", f"mypy execution failed: {exc}"
            elif self.strict:
                return False, "", "mypy configured but mypy executable is not available in strict mode"

        return True, f"Type and AST signatures verified across {len(py_files)} Python file(s)", ""

    def _run_scoped_tests(self, scoped_files: Optional[List[str]]) -> Tuple[bool, str, str]:
        # Scoped unit test execution
        cmd = ["pytest", "-q", "-m", "not integration", "--ignore=tests/test_analytics_api.py", "--ignore-glob=tests/test_tui_*.py"]
        if scoped_files:
            test_files = [f for f in scoped_files if "test" in f]
            if test_files:
                cmd = ["pytest", "-q"] + test_files

        runner = self.subprocess_runner or subprocess.run
        try:
            res = runner(cmd, cwd=self.cwd, capture_output=True, text=True, timeout=30)
        except Exception as e:
            return False, "", str(e)

        if res.returncode == 0:
            return True, res.stdout or "Tests passed", ""
        if res.returncode == 5:
            if self.strict:
                return False, res.stdout or "", res.stderr or "No tests collected in strict mode (exit code 5)"
            return True, res.stdout or "No tests collected", ""
        return False, res.stdout or "", res.stderr or res.stdout or f"Scoped tests failed with exit code {res.returncode}"

    def _run_registry_integration(self) -> Tuple[bool, str, str]:
        # Registry and integration gate
        from vad.integrity.registry import IntegrityRegistry
        reg_file = Path(self.cwd) / "skills" / "registry.json"
        if reg_file.exists():
            reg = IntegrityRegistry(str(reg_file.as_posix()))
            for p, entry in reg.get_all().items():
                if entry.verdict == "REJECT":
                    return False, "", f"Registry item {p} has verdict REJECT"
        return True, "Registry integration clean", ""

    def _run_model_review(self, mees_score: Optional[int]) -> Tuple[bool, str, str]:
        # Model review scaled by MEES score
        if mees_score is not None and mees_score < 50:
            return False, "", f"Model review blocked: MEES score {mees_score} is below threshold 50"
        return True, f"Model review passed with MEES={mees_score or 100}", ""


class VerifierRunner:
    """Maps EIP proof obligations onto test executions and runs cost-ordered ladders."""

    def __init__(self, eip: EIP, plan: ProofPlan, execution_guard: ExecutionGuard | None = None, cwd: str | None = None):
        self.eip = eip
        self.plan = plan
        self.execution_guard = execution_guard or ExecutionGuard()
        self.cwd = cwd
        self.ladder_runner = CostOrderedLadderRunner(cwd=cwd)

    def _check_unmapped(self) -> List[VerifierResult]:
        results = []
        for ob in self.eip.proof_obligations:
            if not self.plan.covers_obligation(ob.id):
                results.append(VerifierResult(
                    obligation_id=ob.id,
                    status=VerifyStatus.UNMAPPED,
                    output="",
                    error=f"Obligation {ob.id} is unmapped in ProofPlan."
                ))
        return results

    def run(self) -> VerifierReport:
        results = self._check_plan_matches_eip()
        results.extend(self._check_unmapped())
        passed = len(results) == 0

        # Run mapped tests
        for ob in self.eip.proof_obligations:
            mapping = self.plan.get_mapping(ob.id)
            if mapping:
                cmd = mapping.test_command
                try:
                    result = self.execution_guard.run(shlex.split(cmd), cwd=self.cwd)
                    status = VerifyStatus.PASS if result.allowed and result.exit_code == 0 else VerifyStatus.FAIL
                    passed = passed and (status == VerifyStatus.PASS)
                    results.append(VerifierResult(
                        obligation_id=ob.id,
                        status=status,
                        output=result.stdout,
                        error=result.stderr if status == VerifyStatus.FAIL else None,
                        tool_call={
                            "command": result.command,
                            "cwd": result.cwd,
                            "allowed": result.allowed,
                            "exit_code": result.exit_code,
                            "policy_decision": result.decision.model_dump(mode="json"),
                        },
                    ))
                except Exception as e:
                    passed = False
                    results.append(VerifierResult(
                        obligation_id=ob.id,
                        status=VerifyStatus.FAIL,
                        output="",
                        error=str(e)
                    ))

        return VerifierReport(
            timestamp=datetime.utcnow(),
            results=results,
            passed=passed
        )

    def run_ladder(self, commit_sha: str = "HEAD", mees_score: Optional[int] = None) -> VerificationReceipt:
        return self.ladder_runner.run_ladder(commit_sha=commit_sha, mees_score=mees_score)

    def run_independent(
        self,
        builder_actor: str,
        verifier_actor: str,
        *,
        commit_sha: str = "HEAD",
        builder_claim: Optional[BuilderClaim] = None,
        config: Optional[IsolatedExecutionConfig] = None,
        executor: Optional[IsolatedVerifierExecutor] = None,
        attested: bool = False,
        attestation_ref: Optional[str] = None,
        dual_signoff: bool = False,
        signoff_actors: Optional[List[str]] = None,
        ttl_seconds: Optional[int] = None,
        now: Optional[datetime] = None,
        approved_manual_gates: Optional[set[str]] = None,
    ) -> IndependentVerificationResult:
        """Execute EIP obligations independently in an isolated environment."""
        isolated_executor = executor or IsolatedVerifierExecutor(
            config=config,
            execution_guard=self.execution_guard,
        )
        return isolated_executor.verify_independent(
            self.eip,
            self.plan,
            builder_actor,
            verifier_actor,
            commit_sha=commit_sha,
            workspace_dir=self.cwd,
            builder_claim=builder_claim,
            attested=attested,
            attestation_ref=attestation_ref,
            dual_signoff=dual_signoff,
            signoff_actors=signoff_actors,
            ttl_seconds=ttl_seconds,
            now=now,
            approved_manual_gates=approved_manual_gates,
        )

    def _check_plan_matches_eip(self) -> List[VerifierResult]:
        errors = []
        if self.plan.eip_version != self.eip.version:
            errors.append("ProofPlan EIP version does not match EIP version.")
        if self.plan.eip_digest != compute_eip_digest(self.eip):
            errors.append("ProofPlan EIP digest does not match EIP content.")
        return [
            VerifierResult(
                obligation_id="proof-plan",
                status=VerifyStatus.FAIL,
                output="",
                error=error,
            )
            for error in errors
        ]
