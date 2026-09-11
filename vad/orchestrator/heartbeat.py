"""Heartbeat monitor: Tracks parallel agent liveness and resets stale locks."""

from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional
import json
from pydantic import BaseModel
from vad.orchestrator.dep_graph import DependencyGraph, TaskStatus


class HeartbeatRecord(BaseModel):
    task_id: str
    agent_id: str
    last_ping: datetime
    ping_count: int = 1


class HeartbeatMonitor:
    """Monitors heartbeat pings from executing agents and handles automatic crash recovery."""

    def __init__(
        self,
        graph: DependencyGraph,
        timeout_seconds: float = 600.0,
        state_file: Optional[str] = None,
    ):
        self.graph = graph
        self.timeout_seconds = timeout_seconds
        self.state_file = state_file
        self._records: Dict[str, HeartbeatRecord] = {}

    def ping(self, task_id: str, agent_id: str) -> bool:
        task = self.graph.get_task(task_id)
        if not task or task.status != TaskStatus.LOCKED:
            return False

        now = datetime.now(timezone.utc)
        if task_id in self._records:
            rec = self._records[task_id]
            rec.last_ping = now
            rec.ping_count += 1
        else:
            self._records[task_id] = HeartbeatRecord(
                task_id=task_id,
                agent_id=agent_id,
                last_ping=now,
                ping_count=1,
            )

        self.graph.heartbeat(task_id)
        self._save_state()
        return True

    def get_record(self, task_id: str) -> Optional[HeartbeatRecord]:
        return self._records.get(task_id)

    def check_all(self) -> List[str]:
        """Checks for expired heartbeats and triggers DAG stale lock expiry."""
        now = datetime.now(timezone.utc)
        expired_task_ids = []

        for task_id, rec in list(self._records.items()):
            delta = (now - rec.last_ping).total_seconds()
            if delta > self.timeout_seconds:
                expired_task_ids.append(task_id)
                del self._records[task_id]

        # Reset in DAG
        graph_expired = self.graph.expire_stale_locks(timeout_seconds=self.timeout_seconds)
        combined = list(set(expired_task_ids + graph_expired))
        self._save_state()
        return combined

    def unregister(self, task_id: str) -> None:
        if task_id in self._records:
            del self._records[task_id]
            self._save_state()

    def _save_state(self) -> None:
        if not self.state_file:
            return
        p = Path(self.state_file)
        p.parent.mkdir(parents=True, exist_ok=True)
        data = {
            task_id: {
                "task_id": rec.task_id,
                "agent_id": rec.agent_id,
                "last_ping": rec.last_ping.isoformat(),
                "ping_count": rec.ping_count,
            }
            for task_id, rec in self._records.items()
        }
        with open(p, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
