import subprocess
from pydantic import BaseModel, Field

from vad.policy.decisions import PolicyDecision
from vad.policy.engine import PolicyEngine


class ExecutionResult(BaseModel):
    command: list[str]
    allowed: bool
    cwd: str | None = None
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    decision: PolicyDecision


class ExecutionGuard:
    def __init__(self, policy_path: str = "policies/vad.rego", allowed_executables: set[str] | None = None):
        self.policy_path = policy_path
        self.allowed_executables = allowed_executables or {"pytest", "python", "python3"}
        self.engine = PolicyEngine()

    def run(self, command: list[str], cwd: str | None = None) -> ExecutionResult:
        decision = self.check(command)
        if not decision.allow:
            return ExecutionResult(command=command, cwd=cwd, allowed=False, decision=decision, stderr="; ".join(decision.denials))

        proc = subprocess.run(command, cwd=cwd, capture_output=True, text=True, check=False)
        return ExecutionResult(
            command=command,
            cwd=cwd,
            allowed=True,
            exit_code=proc.returncode,
            stdout=proc.stdout,
            stderr=proc.stderr,
            decision=decision,
        )

    def check(self, command: list[str]) -> PolicyDecision:
        if not command:
            return PolicyDecision(allow=False, denials=["empty command"])

        executable = command[0]
        if executable not in self.allowed_executables:
            return PolicyDecision(allow=False, denials=[f"executable not allowed: {executable}"])

        if any(_contains_shell_control(part) for part in command):
            return PolicyDecision(allow=False, denials=["shell control syntax is not allowed"])

        policy_tool = "shell" if executable in {"sh", "bash", "powershell", "cmd"} else executable
        decision = self.engine.evaluate_decision(self.policy_path, {"action": "use_tool", "tool": policy_tool})
        if decision.denials:
            return decision

        return PolicyDecision(allow=True, reasons=[f"executable allowed: {executable}"])


def _contains_shell_control(value: str) -> bool:
    return any(token in value for token in (";", "&&", "||", "|", "$(", "`", ">", "<"))
