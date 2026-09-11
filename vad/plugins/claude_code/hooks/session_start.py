"""SessionStart Hook: Injects in-progress orchestrator tasks and historical skill ledger memory into Claude Code sessions."""

import json
from pathlib import Path
import sys
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field
from vad.memory.ledger_retriever import LedgerRetriever
from vad.orchestrator.cli_orchestrator import TrackerMarkdownSync
from vad.orchestrator.dep_graph import TaskStatus


class SessionStartResult(BaseModel):
    session_id: str
    injected_context: str
    in_progress_tasks: List[str] = Field(default_factory=list)
    has_active_eip: bool = False


class SessionStartHook:
    """Claude Code SessionStart hook configuring the agent context with live DAG tasks and historical learnings."""

    def __init__(
        self,
        retriever: Optional[LedgerRetriever] = None,
        tracker_path: str = "planning_artifacts/implementation_tracker.md",
    ):
        self.retriever = retriever or LedgerRetriever()
        self.tracker_path = tracker_path

    def process(self, payload: Dict[str, Any]) -> SessionStartResult:
        session_id = payload.get("session_id", "session-init")
        repo = payload.get("repo", "voice-agentic")
        eip_name = payload.get("eip_name")

        # 1. Scan implementation_tracker.md for locked/in-progress tasks
        in_progress = []
        try:
            tasks = TrackerMarkdownSync.parse_tasks(self.tracker_path)
            in_progress = [t.id for t in tasks if t.status in (TaskStatus.LOCKED, TaskStatus.READY)]
        except Exception:
            pass

        # 2. Retrieve historical execution memory from Skill Ledger
        ledger_memory = self.retriever.format_for_session_start(repo)

        # 3. Assemble context
        sections = [
            "# Verified Agentic Development (VAD) Session Context",
            f"Active Repository: `{repo}`",
        ]

        if eip_name:
            sections.append(f"Bound Intent: Executable Intent Package `{eip_name}` is active.")

        if in_progress:
            sections.append(
                f"Currently Pending/In-Progress Orchestrator Tasks: {', '.join(in_progress[:5])}"
            )

        if ledger_memory:
            sections.append(ledger_memory)

        injected = "\n\n".join(sections)

        return SessionStartResult(
            session_id=session_id,
            injected_context=injected,
            in_progress_tasks=in_progress,
            has_active_eip=bool(eip_name),
        )


def main(argv: Optional[list] = None) -> int:
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
    except Exception:
        payload = {}

    hook = SessionStartHook()
    result = hook.process(payload)
    print(json.dumps(result.model_dump()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
