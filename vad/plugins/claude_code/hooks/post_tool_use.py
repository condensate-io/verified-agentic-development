"""PostToolUse Hook: Captures execution telemetry, writes audit events, and updates the skill ledger."""

import json
import sys
from typing import Any, Dict, Optional
from vad.audit.local_audit import AuditEvent, LocalAuditLogger
from vad.memory.skill_ledger import SkillLedger, SkillLedgerEntry, SkillOutcome


class PostToolUseHook:
    """Claude Code PostToolUse hook executing non-blocking telemetry, audit persistence, and skill outcomes."""

    def __init__(
        self,
        audit_logger: Optional[LocalAuditLogger] = None,
        skill_ledger: Optional[SkillLedger] = None,
    ):
        self.audit_logger = audit_logger or LocalAuditLogger()
        self.skill_ledger = skill_ledger or SkillLedger()

    def process(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        session_id = payload.get("session_id", "unknown-session")
        agent_id = payload.get("agent_id", "claude-code")
        tool_name = payload.get("tool_name", "")
        tool_input = payload.get("tool_input", {})
        tool_output = payload.get("tool_output", "")
        exit_code = payload.get("exit_code", 0)
        duration_ms = float(payload.get("duration_ms", 0.0))
        repo = payload.get("repo", "")

        outcome_str = "success" if exit_code == 0 else "failure"

        # 1. Non-blocking audit record
        try:
            event = AuditEvent(
                session_id=session_id,
                agent_id=agent_id,
                action="PostToolUse",
                tool=tool_name,
                outcome=outcome_str,
                metadata={
                    "duration_ms": duration_ms,
                    "exit_code": exit_code,
                    "tool_input_keys": list(tool_input.keys()) if isinstance(tool_input, dict) else [],
                },
            )
            self.audit_logger.log(event)
        except Exception:
            pass

        # 2. Write skill outcome to SkillLedger
        try:
            skill_name = tool_input.get("skill_name") or tool_name
            error_snippet = None
            if exit_code != 0:
                error_snippet = str(tool_output)[:200] if tool_output else "Non-zero exit code"

            ledger_entry = SkillLedgerEntry(
                session_id=session_id,
                skill_name=skill_name,
                agent_id=agent_id,
                outcome=SkillOutcome.SUCCESS if exit_code == 0 else SkillOutcome.FAILURE,
                error_snippet=error_snippet,
                duration_ms=duration_ms,
                repo=repo,
                parameters=tool_input if isinstance(tool_input, dict) else {},
            )
            self.skill_ledger.record(ledger_entry)
        except Exception:
            pass

        return {"status": "recorded", "exit_code": 0}


def main(argv: Optional[list] = None) -> int:
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
    except Exception:
        payload = {}

    hook = PostToolUseHook()
    result = hook.process(payload)
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
