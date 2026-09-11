"""Staged Commit Manager: Enforces human-in-the-loop safety boundary preventing autonomous git push."""

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Dict, List, Optional
from pydantic import BaseModel, Field
from vad.orchestrator.dep_graph import DependencyGraph, TaskStatus
from vad.review.dsse_attestation import DSSEEnvelope


class StagedCommit(BaseModel):
    id: str
    task_id: str
    commit_sha: str
    branch: str
    summary: str
    files_changed: List[str] = Field(default_factory=list)
    diff_stat: str = ""
    staged_at: datetime
    dsse_envelope: Optional[DSSEEnvelope] = None
    confirmed: bool = False
    confirmed_by: Optional[str] = None
    pushed_at: Optional[datetime] = None


class StagedCommitManager:
    """Manages staged commits that are completely verified locally but held awaiting operator confirmation."""

    def __init__(self, graph: DependencyGraph, storage_file: Optional[str] = None):
        self.graph = graph
        self.storage_file = storage_file
        self._staged: Dict[str, StagedCommit] = {}

    def stage_commit(
        self,
        task_id: str,
        commit_sha: str,
        branch: str,
        summary: str,
        files_changed: List[str],
        diff_stat: str = "",
        dsse_envelope: Optional[DSSEEnvelope] = None,
    ) -> StagedCommit:
        # Move task to STAGED in DAG
        task = self.graph.get_task(task_id)
        if task:
            task.status = TaskStatus.STAGED

        staged_id = f"stage-{task_id}-{commit_sha[:8]}"
        item = StagedCommit(
            id=staged_id,
            task_id=task_id,
            commit_sha=commit_sha,
            branch=branch,
            summary=summary,
            files_changed=files_changed,
            diff_stat=diff_stat,
            staged_at=datetime.now(timezone.utc),
            dsse_envelope=dsse_envelope,
            confirmed=False,
        )
        self._staged[staged_id] = item
        self._save()
        return item

    def get_staged_items(self, only_unconfirmed: bool = True) -> List[StagedCommit]:
        if only_unconfirmed:
            return [sc for sc in self._staged.values() if not sc.confirmed]
        return list(self._staged.values())

    def get_staged_item(self, staged_id: str) -> Optional[StagedCommit]:
        return self._staged.get(staged_id)

    def _save(self) -> None:
        if not self.storage_file:
            return
        p = Path(self.storage_file)
        p.parent.mkdir(parents=True, exist_ok=True)
        data = {k: v.model_dump(mode="json") for k, v in self._staged.items()}
        with open(p, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
