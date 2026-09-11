"""SubagentStop Hook: Validates subagent outputs and ensures evidence artifacts are persisted."""

import json
from pathlib import Path
import sys
from typing import Any, Dict, Optional
from pydantic import BaseModel


class SubagentStopResult(BaseModel):
    allowed: bool
    reason: str
    evidence_recorded: bool
    evidence_file: Optional[str] = None


class SubagentStopHook:
    """Claude Code SubagentStop hook enforcing that spawned subagents produce verifiable evidence."""

    def __init__(self, evidence_dir: str = "evidence"):
        self.evidence_dir = Path(evidence_dir)
        self.evidence_dir.mkdir(parents=True, exist_ok=True)

    def process(self, payload: Dict[str, Any]) -> SubagentStopResult:
        subagent_id = payload.get("subagent_id", "subagent-unknown")
        task_id = payload.get("task_id", subagent_id)
        status = payload.get("status", "success")
        evidence_data = payload.get("evidence")

        # Invariant: Successful subagents must produce evidence
        if status == "success" and not evidence_data:
            return SubagentStopResult(
                allowed=False,
                reason=f"Subagent '{subagent_id}' completed successfully but produced no evidence artifact",
                evidence_recorded=False,
            )

        evidence_path = None
        if evidence_data:
            target_file = self.evidence_dir / f"{task_id}.json"
            try:
                with open(target_file, "w", encoding="utf-8") as f:
                    json.dump(
                        {
                            "id": task_id,
                            "subagent_id": subagent_id,
                            "status": status,
                            "evidence": evidence_data,
                        },
                        f,
                        indent=2,
                    )
                evidence_path = str(target_file)
            except Exception as e:
                return SubagentStopResult(
                    allowed=False,
                    reason=f"Failed to persist evidence: {str(e)}",
                    evidence_recorded=False,
                )

        return SubagentStopResult(
            allowed=True,
            reason="Subagent termination approved and evidence persisted",
            evidence_recorded=bool(evidence_path),
            evidence_file=evidence_path,
        )


def main(argv: Optional[list] = None) -> int:
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
    except Exception:
        payload = {}

    hook = SubagentStopHook()
    result = hook.process(payload)
    print(json.dumps(result.model_dump()))
    return 0 if result.allowed else 1


if __name__ == "__main__":
    sys.exit(main())
