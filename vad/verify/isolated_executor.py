"""Isolated verifier execution in offline snapshot sandbox or disposable Docker containers."""

from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import shlex
import shutil
import subprocess
import sys
import tempfile
from typing import Callable, Literal
import uuid

from pydantic import BaseModel, Field

from vad.contracts.models import AssuranceLevel, EIP
from vad.guards.execution import ExecutionGuard
from vad.proof.plan import ProofPlan, VerifyStatus, compute_eip_digest
from vad.verify.freshness import _ensure_utc, compute_proof_plan_digest
from vad.verify.report import (
    BuilderClaim,
    IndependentVerificationResult,
    VerifierResult,
)

IGNORED_TREE_DIRS = frozenset(
    {
        ".git",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".venv",
        "venv",
        "node_modules",
        ".vad",
    }
)

DEPENDENCY_MANIFEST_NAMES = (
    "pyproject.toml",
    "requirements.txt",
    "poetry.lock",
    "package-lock.json",
)


def compute_tree_hash(workspace_dir: str | Path) -> str:
    """Compute a deterministic SHA-256 hash over non-ignored workspace files."""
    root = Path(workspace_dir).resolve()
    if not root.exists():
        return hashlib.sha256(b"missing-workspace").hexdigest()

    entries: list[tuple[str, str]] = []
    for current_root, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in IGNORED_TREE_DIRS)
        current_path = Path(current_root)
        for filename in sorted(files):
            file_path = current_path / filename
            if file_path.is_symlink():
                continue
            rel_path = file_path.relative_to(root).as_posix()
            file_hash = hashlib.sha256(file_path.read_bytes()).hexdigest()
            entries.append((rel_path, file_hash))

    canonical = json.dumps(entries, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def compute_env_digest(
    config: "IsolatedExecutionConfig",
    workspace_dir: str | Path | None = None,
) -> str:
    """Compute a deterministic SHA-256 digest of the verifier execution environment."""
    manifests: dict[str, str] = {}
    if workspace_dir is not None:
        root = Path(workspace_dir).resolve()
        for name in DEPENDENCY_MANIFEST_NAMES:
            manifest_path = root / name
            if manifest_path.is_file():
                manifests[name] = hashlib.sha256(manifest_path.read_bytes()).hexdigest()

    env_payload = {
        "mode": config.mode,
        "docker_image": config.docker_image if config.mode == "docker" else None,
        "network_disabled": config.network_disabled,
        "read_only_mount": config.read_only_mount,
        "python_version": platform.python_version(),
        "platform": sys.platform,
        "manifests": manifests,
    }
    canonical = json.dumps(env_payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _copy_workspace_snapshot(src_dir: Path, dst_dir: Path) -> None:
    """Copy non-ignored workspace files into an isolated snapshot directory."""
    if not src_dir.exists():
        return

    for current_root, dirs, files in os.walk(src_dir):
        dirs[:] = sorted(d for d in dirs if d not in IGNORED_TREE_DIRS)
        current_path = Path(current_root)
        rel_dir = current_path.relative_to(src_dir)
        target_dir = dst_dir / rel_dir
        target_dir.mkdir(parents=True, exist_ok=True)
        for filename in sorted(files):
            src_file = current_path / filename
            if src_file.is_symlink():
                continue
            shutil.copy2(src_file, target_dir / filename)


class IsolatedExecutionConfig(BaseModel):
    """Configuration for isolated independent verification execution."""

    mode: Literal["sandbox", "docker"] = "sandbox"
    docker_image: str = "vad-runner:3.11"
    timeout_seconds: int = Field(default=60, ge=1)
    read_only_mount: bool = True
    network_disabled: bool = True


class IsolatedVerifierExecutor:
    """Executes proof obligations independently inside a sandbox snapshot or Docker container."""

    def __init__(
        self,
        config: IsolatedExecutionConfig | None = None,
        execution_guard: ExecutionGuard | None = None,
        subprocess_runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
    ):
        self.config = config or IsolatedExecutionConfig()
        self.execution_guard = execution_guard or ExecutionGuard()
        self.subprocess_runner = subprocess_runner

    def build_docker_command(self, workspace_dir: str | Path, test_command: str) -> list[str]:
        """Build a hardened docker run command with network/mount isolation flags."""
        resolved_workspace = Path(workspace_dir).resolve().as_posix()
        cmd = ["docker", "run", "--rm"]
        if self.config.network_disabled:
            cmd.append("--network=none")
        if self.config.read_only_mount:
            cmd.extend(
                [
                    "--read-only",
                    "--tmpfs",
                    "/tmp:rw,noexec,nosuid",
                    "-v",
                    f"{resolved_workspace}:/workspace:ro",
                ]
            )
        else:
            cmd.extend(["-v", f"{resolved_workspace}:/workspace"])
        cmd.extend(["-w", "/workspace", self.config.docker_image])
        cmd.extend(shlex.split(test_command))
        return cmd

    def verify_independent(
        self,
        eip: EIP,
        proof_plan: ProofPlan,
        builder_actor: str,
        verifier_actor: str,
        *,
        commit_sha: str = "HEAD",
        workspace_dir: str | Path | None = None,
        builder_claim: BuilderClaim | None = None,
        tree_hash: str | None = None,
        env_digest: str | None = None,
        attested: bool = False,
        attestation_ref: str | None = None,
        dual_signoff: bool = False,
        signoff_actors: list[str] | None = None,
        ttl_seconds: int | None = None,
        now: datetime | None = None,
        approved_manual_gates: set[str] | None = None,
    ) -> IndependentVerificationResult:
        """Execute all EIP proof obligations independently and emit a stamped result.

        Enforces strict actor separation (verifier != builder) and ignores
        builder_claim.claimed_passed so self-reported claims can never substitute
        for independent execution.
        """
        self._enforce_actor_separation(builder_actor, verifier_actor, builder_claim)

        source_dir = Path(workspace_dir).resolve() if workspace_dir else Path.cwd().resolve()
        eip_digest = compute_eip_digest(eip)
        proof_plan_digest = compute_proof_plan_digest(proof_plan)
        verified_at = _ensure_utc(now or datetime.now(timezone.utc))
        effective_ttl = (
            ttl_seconds
            if ttl_seconds is not None
            else eip.effective_freshness_policy().max_evidence_age_seconds
        )
        expires_at = verified_at + timedelta(seconds=effective_ttl)

        results = self._check_plan_matches_eip(eip, proof_plan, eip_digest)
        results.extend(self._check_unmapped(eip, proof_plan))
        has_setup_failure = len(results) > 0

        if self.config.mode == "sandbox":
            with tempfile.TemporaryDirectory(prefix="vad_isolated_") as tmp_dir:
                snapshot_dir = Path(tmp_dir) / "workspace"
                snapshot_dir.mkdir(parents=True, exist_ok=True)
                _copy_workspace_snapshot(source_dir, snapshot_dir)
                computed_tree_hash = tree_hash or compute_tree_hash(snapshot_dir)
                computed_env_digest = env_digest or compute_env_digest(self.config, snapshot_dir)
                exec_results, obligations_ok = self._execute_obligations(
                    eip=eip,
                    proof_plan=proof_plan,
                    exec_dir=snapshot_dir,
                    approved_manual_gates=approved_manual_gates or set(),
                )
                results.extend(exec_results)
        else:
            computed_tree_hash = tree_hash or compute_tree_hash(source_dir)
            computed_env_digest = env_digest or compute_env_digest(self.config, source_dir)
            exec_results, obligations_ok = self._execute_obligations(
                eip=eip,
                proof_plan=proof_plan,
                exec_dir=source_dir,
                approved_manual_gates=approved_manual_gates or set(),
            )
            results.extend(exec_results)

        passed = (not has_setup_failure) and obligations_ok
        assurance_achieved = self._determine_assurance_level(
            passed=passed,
            builder_actor=builder_actor,
            attested=attested,
            attestation_ref=attestation_ref,
            dual_signoff=dual_signoff,
            signoff_actors=signoff_actors,
        )

        return IndependentVerificationResult(
            verification_id=f"ver-{uuid.uuid4().hex[:12]}",
            eip_digest=eip_digest,
            proof_plan_digest=proof_plan_digest,
            commit_sha=commit_sha,
            tree_hash=computed_tree_hash,
            env_digest=computed_env_digest,
            builder_actor=builder_actor,
            verifier_actor=verifier_actor,
            isolated_environment=True,
            assurance_achieved=assurance_achieved,
            passed=passed,
            obligation_results=results,
            verified_at=verified_at,
            expires_at=expires_at,
        ).stamp()

    @staticmethod
    def _enforce_actor_separation(
        builder_actor: str,
        verifier_actor: str,
        builder_claim: BuilderClaim | None,
    ) -> None:
        if not builder_actor.strip() or not verifier_actor.strip():
            raise PermissionError("builder_actor and verifier_actor must be non-empty")
        if verifier_actor.strip() == builder_actor.strip():
            raise PermissionError(
                "verifier_actor must be distinct from builder_actor for independent verification"
            )
        if builder_claim is not None and builder_claim.builder_actor.strip() == verifier_actor.strip():
            raise PermissionError(
                "verifier_actor cannot match builder_claim.builder_actor"
            )

    @staticmethod
    def _check_plan_matches_eip(
        eip: EIP,
        proof_plan: ProofPlan,
        eip_digest: str,
    ) -> list[VerifierResult]:
        errors: list[str] = []
        if proof_plan.eip_version != eip.version:
            errors.append("ProofPlan EIP version does not match EIP version.")
        if proof_plan.eip_digest != eip_digest:
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

    @staticmethod
    def _check_unmapped(eip: EIP, proof_plan: ProofPlan) -> list[VerifierResult]:
        unmapped: list[VerifierResult] = []
        for ob in eip.proof_obligations:
            if not proof_plan.covers_obligation(ob.id):
                unmapped.append(
                    VerifierResult(
                        obligation_id=ob.id,
                        status=VerifyStatus.UNMAPPED,
                        output="",
                        error=f"Obligation {ob.id} is unmapped in ProofPlan.",
                    )
                )
        return unmapped

    def _execute_obligations(
        self,
        *,
        eip: EIP,
        proof_plan: ProofPlan,
        exec_dir: Path,
        approved_manual_gates: set[str],
    ) -> tuple[list[VerifierResult], bool]:
        results: list[VerifierResult] = []
        all_passed = True

        for ob in eip.proof_obligations:
            mapping = proof_plan.get_mapping(ob.id)
            if mapping is None:
                if ob.id in proof_plan.required_manual_gates:
                    if ob.id in approved_manual_gates:
                        results.append(
                            VerifierResult(
                                obligation_id=ob.id,
                                status=VerifyStatus.PASS,
                                output=f"Manual gate {ob.id} approved.",
                            )
                        )
                    else:
                        all_passed = False
                        results.append(
                            VerifierResult(
                                obligation_id=ob.id,
                                status=VerifyStatus.PENDING,
                                output="",
                                error=f"Manual gate {ob.id} requires explicit sign-off.",
                            )
                        )
                continue

            ob_result = self._execute_single_command(ob.id, mapping.test_command, exec_dir)
            if ob_result.status != VerifyStatus.PASS:
                all_passed = False
            results.append(ob_result)

        return results, all_passed

    def _execute_single_command(
        self,
        obligation_id: str,
        test_command: str,
        exec_dir: Path,
    ) -> VerifierResult:
        try:
            cmd_parts = shlex.split(test_command)
        except ValueError as exc:
            return VerifierResult(
                obligation_id=obligation_id,
                status=VerifyStatus.FAIL,
                output="",
                error=f"Invalid test command syntax: {exc}",
            )

        if self.config.mode == "docker":
            return self._run_in_docker(obligation_id, test_command, cmd_parts, exec_dir)
        return self._run_in_sandbox(obligation_id, cmd_parts, exec_dir)

    def _run_in_sandbox(
        self,
        obligation_id: str,
        cmd_parts: list[str],
        exec_dir: Path,
    ) -> VerifierResult:
        try:
            if self.subprocess_runner is not None:
                decision = self.execution_guard.check(cmd_parts)
                if not decision.allow:
                    return VerifierResult(
                        obligation_id=obligation_id,
                        status=VerifyStatus.FAIL,
                        output="",
                        error="; ".join(decision.denials),
                        tool_call={
                            "command": cmd_parts,
                            "cwd": str(exec_dir),
                            "allowed": False,
                            "exit_code": None,
                            "policy_decision": decision.model_dump(mode="json"),
                        },
                    )
                proc = self.subprocess_runner(
                    cmd_parts,
                    cwd=str(exec_dir),
                    capture_output=True,
                    text=True,
                    timeout=self.config.timeout_seconds,
                    check=False,
                )
                status = VerifyStatus.PASS if proc.returncode == 0 else VerifyStatus.FAIL
                return VerifierResult(
                    obligation_id=obligation_id,
                    status=status,
                    output=proc.stdout or "",
                    error=(proc.stderr or proc.stdout) if status == VerifyStatus.FAIL else None,
                    tool_call={
                        "command": cmd_parts,
                        "cwd": str(exec_dir),
                        "allowed": True,
                        "exit_code": proc.returncode,
                        "policy_decision": decision.model_dump(mode="json"),
                    },
                )

            exec_result = self.execution_guard.run(cmd_parts, cwd=str(exec_dir))
            status = (
                VerifyStatus.PASS
                if exec_result.allowed and exec_result.exit_code == 0
                else VerifyStatus.FAIL
            )
            error_msg = None
            if status == VerifyStatus.FAIL:
                error_msg = exec_result.stderr or exec_result.stdout or "Command failed"
            return VerifierResult(
                obligation_id=obligation_id,
                status=status,
                output=exec_result.stdout,
                error=error_msg,
                tool_call={
                    "command": exec_result.command,
                    "cwd": exec_result.cwd,
                    "allowed": exec_result.allowed,
                    "exit_code": exec_result.exit_code,
                    "policy_decision": exec_result.decision.model_dump(mode="json"),
                },
            )
        except Exception as exc:
            return VerifierResult(
                obligation_id=obligation_id,
                status=VerifyStatus.FAIL,
                output="",
                error=str(exc),
            )

    def _run_in_docker(
        self,
        obligation_id: str,
        test_command: str,
        cmd_parts: list[str],
        exec_dir: Path,
    ) -> VerifierResult:
        try:
            decision = self.execution_guard.check(cmd_parts)
            if not decision.allow:
                return VerifierResult(
                    obligation_id=obligation_id,
                    status=VerifyStatus.FAIL,
                    output="",
                    error="; ".join(decision.denials),
                    tool_call={
                        "command": cmd_parts,
                        "cwd": str(exec_dir),
                        "allowed": False,
                        "exit_code": None,
                        "policy_decision": decision.model_dump(mode="json"),
                    },
                )

            docker_cmd = self.build_docker_command(exec_dir, test_command)
            runner = self.subprocess_runner or subprocess.run
            proc = runner(
                docker_cmd,
                cwd=str(exec_dir),
                capture_output=True,
                text=True,
                timeout=self.config.timeout_seconds,
                check=False,
            )
            status = VerifyStatus.PASS if proc.returncode == 0 else VerifyStatus.FAIL
            return VerifierResult(
                obligation_id=obligation_id,
                status=status,
                output=proc.stdout or "",
                error=(proc.stderr or proc.stdout) if status == VerifyStatus.FAIL else None,
                tool_call={
                    "command": docker_cmd,
                    "cwd": str(exec_dir),
                    "allowed": True,
                    "exit_code": proc.returncode,
                    "policy_decision": decision.model_dump(mode="json"),
                },
            )
        except Exception as exc:
            return VerifierResult(
                obligation_id=obligation_id,
                status=VerifyStatus.FAIL,
                output="",
                error=str(exc),
            )

    @staticmethod
    def _determine_assurance_level(
        *,
        passed: bool,
        builder_actor: str,
        attested: bool,
        attestation_ref: str | None,
        dual_signoff: bool,
        signoff_actors: list[str] | None,
    ) -> AssuranceLevel:
        if not passed:
            return AssuranceLevel.A1_BASIC

        has_attestation = attested or bool(attestation_ref and attestation_ref.strip())
        distinct_signoffs = {
            actor.strip()
            for actor in (signoff_actors or [])
            if actor.strip() and actor.strip() != builder_actor.strip()
        }
        has_dual_signoff = dual_signoff or len(distinct_signoffs) >= 2

        if has_attestation and has_dual_signoff:
            return AssuranceLevel.A4_RESTRICTED
        if has_attestation:
            return AssuranceLevel.A3_ATTESTED
        return AssuranceLevel.A2_INDEPENDENT
