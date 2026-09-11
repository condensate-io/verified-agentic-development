"""PreToolUse Hook: Gatekeeper validating tool permissions, file integrity, and DoR gates before tool execution."""

import json
import sys
from typing import Any, Dict, Optional
from pydantic import BaseModel
from vad.contracts.models import EIP
from vad.decision.dor_gate import DoRGate
from vad.integrity.integrity_checker import RuntimeIntegrityChecker
from vad.policy.action_governance import ActionGovernanceEngine, GovernanceDecision


class PreToolUseResponse(BaseModel):
    permissionDecision: str  # "allow", "deny", "ask"
    reason: str
    exit_code: int = 0


class PreToolUseHook:
    """Claude Code PreToolUse hook enforcing multi-tier enterprise governance."""

    def __init__(
        self,
        governance_engine: Optional[ActionGovernanceEngine] = None,
        integrity_checker: Optional[RuntimeIntegrityChecker] = None,
        dor_gate: Optional[DoRGate] = None,
    ):
        self.governance = governance_engine or ActionGovernanceEngine()
        self.integrity = integrity_checker or RuntimeIntegrityChecker()
        self.dor_gate = dor_gate or DoRGate()

    def process(self, payload: Dict[str, Any]) -> PreToolUseResponse:
        tool_name = payload.get("tool_name", "")
        tool_input = payload.get("tool_input", {})
        eip_data = payload.get("eip")

        # 1. Integrity Check: inspect tool_input for target scripts/skills
        target_path = tool_input.get("path") or tool_input.get("file_path")
        if target_path:
            chk = self.integrity.check(str(target_path))
            if chk.decision == "block":
                return PreToolUseResponse(
                    permissionDecision="deny",
                    reason=f"[Integrity Gate Block] {chk.reason}",
                    exit_code=2,
                )

        cmd = tool_input.get("command")
        if cmd and isinstance(cmd, str):
            cmd_parts = cmd.split()
            for chk in self.integrity.check_command_paths(cmd_parts):
                if chk.decision == "block":
                    return PreToolUseResponse(
                        permissionDecision="deny",
                        reason=f"[Integrity Gate Block] {chk.reason}",
                        exit_code=2,
                    )

        # 2. DoR Gate Check: if an EIP is attached, verify readiness
        if eip_data:
            try:
                eip = EIP(**eip_data)
                dor_res = self.dor_gate.evaluate(eip)
                if not dor_res.admitted:
                    return PreToolUseResponse(
                        permissionDecision="deny",
                        reason=f"[DoR Gate Block] {dor_res.reason}",
                        exit_code=2,
                    )
            except Exception as e:
                return PreToolUseResponse(
                    permissionDecision="deny",
                    reason=f"[DoR Malformed EIP] {str(e)}",
                    exit_code=2,
                )

        # 3. Action Governance Check (Static Bounds + LLM Safety)
        gov_res = self.governance.evaluate_action(tool_name, tool_input)
        if gov_res.decision == GovernanceDecision.DENY:
            return PreToolUseResponse(
                permissionDecision="deny",
                reason=gov_res.reason,
                exit_code=2,
            )
        elif gov_res.decision == GovernanceDecision.ASK:
            return PreToolUseResponse(
                permissionDecision="ask",
                reason=gov_res.reason,
                exit_code=0,
            )

        return PreToolUseResponse(
            permissionDecision="allow",
            reason="PreToolUse gate passed: Integrity, DoR, and Action Governance satisfied",
            exit_code=0,
        )


def main(argv: Optional[list] = None) -> int:
    try:
        raw_input = sys.stdin.read()
        payload = json.loads(raw_input) if raw_input.strip() else {}
    except Exception:
        payload = {}

    hook = PreToolUseHook()
    response = hook.process(payload)
    print(json.dumps(response.model_dump()))
    return response.exit_code


if __name__ == "__main__":
    sys.exit(main())
