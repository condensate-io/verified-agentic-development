"""Stop Hook: Validates proof obligations, emits session CostReport, and enforces completion criteria."""

import json
import sys
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field
from vad.audit.cost_report import CostReport, CostReportGenerator
from vad.audit.local_audit import LocalAuditLogger


class StopHookResult(BaseModel):
    allow_stop: bool
    reason: str
    cost_report: Optional[CostReport] = None
    unfulfilled_obligations: List[str] = Field(default_factory=list)


class StopHook:
    """Claude Code Stop hook ensuring proof obligations are satisfied and emitting cost reports."""

    def __init__(
        self,
        cost_generator: Optional[CostReportGenerator] = None,
        audit_logger: Optional[LocalAuditLogger] = None,
    ):
        self.cost_generator = cost_generator or CostReportGenerator()
        self.audit_logger = audit_logger or LocalAuditLogger()

    def process(self, payload: Dict[str, Any]) -> StopHookResult:
        session_id = payload.get("session_id", "default-session")
        agent_id = payload.get("agent_id", "claude-code")
        tokens = int(payload.get("tokens_used", 0))
        tool_calls = payload.get("tool_calls", [])
        proof_obligations = payload.get("proof_obligations", [])
        verified_proofs = set(payload.get("verified_proofs", []))

        # 1. Generate and log session CostReport
        report = self.cost_generator.generate_report(
            session_id=session_id,
            agent_id=agent_id,
            tokens=tokens,
            tool_calls=tool_calls,
        )
        self.cost_generator.log_to_audit(report, self.audit_logger)

        # 2. Check proof obligations
        unfulfilled = []
        for req in proof_obligations:
            req_id = req if isinstance(req, str) else req.get("id")
            if req_id and req_id not in verified_proofs:
                unfulfilled.append(req_id)

        if unfulfilled:
            return StopHookResult(
                allow_stop=False,
                reason=f"Premature stop rejected: {len(unfulfilled)} unfulfilled proof obligation(s): {', '.join(unfulfilled)}",
                cost_report=report,
                unfulfilled_obligations=unfulfilled,
            )

        return StopHookResult(
            allow_stop=True,
            reason="Session stop permitted: all proof obligations fulfilled and cost reported.",
            cost_report=report,
            unfulfilled_obligations=[],
        )


def main(argv: Optional[list] = None) -> int:
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
    except Exception:
        payload = {}

    hook = StopHook()
    result = hook.process(payload)
    print(json.dumps(result.model_dump()))
    return 0 if result.allow_stop else 1


if __name__ == "__main__":
    sys.exit(main())
