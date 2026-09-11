"""Skill Ledger: Append-only persistent execution ledger capturing skill invocations and outcomes."""

from datetime import datetime, timezone
from enum import Enum
import json
from pathlib import Path
import threading
from typing import Any, Dict, List, Optional
import uuid
from pydantic import BaseModel, Field


class SkillOutcome(str, Enum):
    SUCCESS = "success"
    FAILURE = "failure"


class SkillLedgerEntry(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    session_id: str
    skill_name: str
    agent_id: str
    outcome: SkillOutcome
    error_snippet: Optional[str] = None
    duration_ms: float = 0.0
    repo: str = ""
    parameters: Dict[str, Any] = Field(default_factory=dict)


class SkillLedger:
    """Records skill invocations and failure patterns to inform session initialization and gap mining."""

    def __init__(self, ledger_path: str = "memory/skill_ledger.jsonl"):
        self.ledger_path = ledger_path
        self._lock = threading.Lock()
        p = Path(self.ledger_path)
        p.parent.mkdir(parents=True, exist_ok=True)

    def record(self, entry: SkillLedgerEntry) -> None:
        with self._lock:
            with open(self.ledger_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry.model_dump()) + "\n")

    def get_entries(
        self,
        skill_name: Optional[str] = None,
        repo: Optional[str] = None,
        outcome: Optional[SkillOutcome] = None,
        limit: int = 100,
    ) -> List[SkillLedgerEntry]:
        p = Path(self.ledger_path)
        if not p.exists():
            return []

        entries = []
        with open(p, "r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    data = json.loads(line.strip())
                    e = SkillLedgerEntry(**data)
                    if skill_name and e.skill_name.lower() != skill_name.lower():
                        continue
                    if repo and e.repo.lower() != repo.lower():
                        continue
                    if outcome and e.outcome != outcome:
                        continue
                    entries.append(e)
                except Exception:
                    continue

        return entries[-limit:]

    def get_failure_rate(self, skill_name: str) -> float:
        entries = self.get_entries(skill_name=skill_name, limit=1000)
        if not entries:
            return 0.0
        failures = sum(1 for e in entries if e.outcome == SkillOutcome.FAILURE)
        return round(failures / len(entries), 4)
