"""Push Gate: git ls-remote plus reflog check before a run enters the staged state."""

from dataclasses import dataclass
import subprocess
from typing import Callable, Optional
from vad.orchestrator.circuit_breaker import CircuitBreaker, get_circuit_breaker


@dataclass
class PushGateResult:
    allowed: bool
    reason: str
    commit_sha: str
    branch: str
    remote_checked: bool = False
    reflog_verified: bool = False


class PushGate:
    """Guards task transition to STAGED by validating git ls-remote connectivity and local reflog history."""

    def __init__(
        self,
        repo_dir: Optional[str] = None,
        remote: str = "origin",
        circuit_breaker: Optional[CircuitBreaker] = None,
        runner: Optional[Callable] = None,
    ):
        self.repo_dir = repo_dir or "."
        self.remote = remote
        self.circuit_breaker = circuit_breaker or get_circuit_breaker()
        self.runner = runner or subprocess.run

    def check_staging_readiness(self, branch: str, commit_sha: str) -> PushGateResult:
        # Check global circuit breaker first
        if self.circuit_breaker.is_tripped():
            return PushGateResult(
                allowed=False,
                reason="Global circuit breaker is TRIPPED due to prior push-gate violations. Staging is locked.",
                commit_sha=commit_sha,
                branch=branch,
            )

        # 1. git ls-remote check
        try:
            ls_res = self.runner(
                ["git", "ls-remote", "--heads", self.remote, branch],
                cwd=self.repo_dir,
                capture_output=True,
                text=True,
                check=False,
            )
            # A returncode != 0 (e.g. fatal error or host uncontactable) blocks staging
            if ls_res.returncode != 0 and "fatal:" in ls_res.stderr.lower():
                self.circuit_breaker.record_violation(f"git ls-remote failed: {ls_res.stderr.strip()}")
                return PushGateResult(
                    allowed=False,
                    reason=f"PushGate ls-remote failed: {ls_res.stderr.strip()}",
                    commit_sha=commit_sha,
                    branch=branch,
                    remote_checked=False,
                )
        except Exception as e:
            self.circuit_breaker.record_violation(f"git ls-remote exception: {str(e)}")
            return PushGateResult(
                allowed=False,
                reason=f"PushGate ls-remote exception: {str(e)}",
                commit_sha=commit_sha,
                branch=branch,
            )

        # 2. git reflog check: ensure commit_sha exists in local reflog
        try:
            reflog_res = self.runner(
                ["git", "reflog", "show", "--format=%H", "-n", "100"],
                cwd=self.repo_dir,
                capture_output=True,
                text=True,
                check=False,
            )
            if reflog_res.returncode != 0:
                self.circuit_breaker.record_violation("git reflog query failed")
                return PushGateResult(
                    allowed=False,
                    reason="PushGate reflog query failed",
                    commit_sha=commit_sha,
                    branch=branch,
                    remote_checked=True,
                )

            reflog_shas = [line.strip().lower() for line in reflog_res.stdout.splitlines() if line.strip()]
            norm_sha = commit_sha.lower()
            # Check if commit_sha or its prefix is in local reflog
            found = any(s.startswith(norm_sha) or norm_sha.startswith(s) for s in reflog_shas)
            if not found:
                self.circuit_breaker.record_violation(
                    f"Commit '{commit_sha}' was not found in local git reflog history"
                )
                return PushGateResult(
                    allowed=False,
                    reason=f"PushGate violation: commit {commit_sha} does not exist in local git reflog",
                    commit_sha=commit_sha,
                    branch=branch,
                    remote_checked=True,
                    reflog_verified=False,
                )
        except Exception as e:
            self.circuit_breaker.record_violation(f"git reflog exception: {str(e)}")
            return PushGateResult(
                allowed=False,
                reason=f"PushGate reflog exception: {str(e)}",
                commit_sha=commit_sha,
                branch=branch,
                remote_checked=True,
            )

        return PushGateResult(
            allowed=True,
            reason="PushGate validated: git ls-remote and reflog verified.",
            commit_sha=commit_sha,
            branch=branch,
            remote_checked=True,
            reflog_verified=True,
        )
