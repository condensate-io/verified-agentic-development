"""PreToolUse Hook: Gatekeeper validating tool permissions, secrets, privacy, integrity, and DoR gates before tool execution."""

import json
import os
import sys
from typing import Any, Dict, Optional
from pydantic import BaseModel, Field

from vad.contracts.models import EIP
from vad.decision.dor_gate import DoRGate
from vad.integrity.integrity_checker import RuntimeIntegrityChecker
from vad.memory.ledger_retriever import LedgerRetriever
from vad.plugins.claude_code.hooks.guards import (
    AgentIntegrityGuard,
    CredentialGuard,
    DestructiveBashGuard,
    EIPReadinessGuard,
    GuardDecision,
    ReadPrivacyGuard,
)
from vad.policy.action_governance import ActionGovernanceEngine, GovernanceDecision


class PreToolUseResponse(BaseModel):
    permissionDecision: str  # "allow", "deny", "ask"
    reason: str
    exit_code: int = 0
    ledger_context: Optional[str] = None


class PreToolUseHook:
    """Claude Code PreToolUse hook enforcing multi-tier enterprise governance and safety guards."""

    def __init__(
        self,
        governance_engine: Optional[ActionGovernanceEngine] = None,
        integrity_checker: Optional[RuntimeIntegrityChecker] = None,
        dor_gate: Optional[DoRGate] = None,
        credential_guard: Optional[CredentialGuard] = None,
        destructive_bash_guard: Optional[DestructiveBashGuard] = None,
        read_privacy_guard: Optional[ReadPrivacyGuard] = None,
        agent_integrity_guard: Optional[AgentIntegrityGuard] = None,
        eip_readiness_guard: Optional[EIPReadinessGuard] = None,
        ledger_retriever: Optional[LedgerRetriever] = None,
    ):
        self.governance = governance_engine or ActionGovernanceEngine()
        self.integrity = integrity_checker or RuntimeIntegrityChecker()
        self.dor_gate = dor_gate or DoRGate()

        # New modular guards
        self.credential_guard = credential_guard or CredentialGuard()
        self.destructive_bash_guard = destructive_bash_guard or DestructiveBashGuard()
        self.read_privacy_guard = read_privacy_guard or ReadPrivacyGuard()
        self.agent_integrity_guard = agent_integrity_guard or AgentIntegrityGuard()
        self.eip_readiness_guard = eip_readiness_guard or EIPReadinessGuard()

        # Ledger retrieval integration (Requirement 8)
        self.ledger_retriever = ledger_retriever or LedgerRetriever()

    def process(self, payload: Dict[str, Any]) -> PreToolUseResponse:
        tool_name = payload.get("tool_name", "")
        tool_input = payload.get("tool_input", {})
        if not isinstance(tool_input, dict):
            tool_input = {}
        eip_data = payload.get("eip")

        # 1. Credential Guard: block Write/Edit payloads with secrets
        cred_res = self.credential_guard.evaluate(tool_name, tool_input)
        if not cred_res.allowed:
            return PreToolUseResponse(
                permissionDecision="deny",
                reason=cred_res.reason,
                exit_code=2,
            )

        # 2. Read Privacy Guard: block reads of .env*, *.pem, id_rsa*
        priv_res = self.read_privacy_guard.evaluate(tool_name, tool_input)
        if not priv_res.allowed:
            return PreToolUseResponse(
                permissionDecision="deny",
                reason=priv_res.reason,
                exit_code=2,
            )

        # 3. Destructive Bash Guard: deny and ask tiers
        bash_res = self.destructive_bash_guard.evaluate(tool_name, tool_input)
        if bash_res.decision == GuardDecision.DENY:
            return PreToolUseResponse(
                permissionDecision="deny",
                reason=bash_res.reason,
                exit_code=2,
            )
        elif bash_res.decision == GuardDecision.ASK:
            return PreToolUseResponse(
                permissionDecision="ask",
                reason=bash_res.reason,
                exit_code=0,
            )

        # 4. Agent Integrity Guard: check custom subagent types
        agent_res = self.agent_integrity_guard.evaluate(tool_name, tool_input)
        if not agent_res.allowed:
            return PreToolUseResponse(
                permissionDecision="deny",
                reason=agent_res.reason,
                exit_code=2,
            )

        # 5. EIP Readiness Guard: check status: ready and no open questions
        eip_res = self.eip_readiness_guard.evaluate(payload)
        if not eip_res.allowed:
            return PreToolUseResponse(
                permissionDecision="deny",
                reason=eip_res.reason,
                exit_code=2,
            )

        # 6. Legacy Integrity Check: inspect tool_input for target scripts/skills
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

        # 7. DoR Gate Check: if an EIP is attached, verify readiness
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

        # 8. Action Governance Check (Static Bounds + LLM Safety)
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

        # 9. Skill Ledger Augmentation (Requirement 8): inject historical caveats if available
        repo = payload.get("repo", "")
        ledger_ctx = ""
        skill_name = tool_input.get("skill_name") or tool_name
        if skill_name:
            failure_rate = self.ledger_retriever.ledger.get_failure_rate(skill_name)
            if failure_rate >= 0.5:
                ledger_ctx = f"Warning: skill '{skill_name}' has high historical failure rate ({failure_rate*100:.1f}%)."

        return PreToolUseResponse(
            permissionDecision="allow",
            reason="PreToolUse gates passed: Credentials, Privacy, Integrity, Destructive Bash, DoR, and Governance satisfied.",
            exit_code=0,
            ledger_context=ledger_ctx if ledger_ctx else None,
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
