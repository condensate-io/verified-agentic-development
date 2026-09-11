"""Dependency Graph (DAG) for parallel task orchestration without worktree isolation."""

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Set
from pydantic import BaseModel, Field


class TaskStatus(str, Enum):
    READY = "ready"
    LOCKED = "locked"
    STAGED = "staged"
    DONE = "done"
    FAILED = "failed"


class TaskNode(BaseModel):
    id: str
    title: str
    dependencies: List[str] = Field(default_factory=list)
    status: TaskStatus = TaskStatus.READY
    assigned_agent: Optional[str] = None
    locked_at: Optional[datetime] = None
    last_heartbeat: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    failure_reason: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class DependencyGraph:
    """Directed Acyclic Graph orchestrating task readiness, locking, and failure recovery."""

    def __init__(self):
        self._tasks: Dict[str, TaskNode] = {}

    def add_task(self, task: TaskNode) -> None:
        if task.id in self._tasks:
            raise ValueError(f"Task with ID '{task.id}' already exists in graph")
        self._tasks[task.id] = task
        if self.has_cycle():
            del self._tasks[task.id]
            raise ValueError(f"Adding task '{task.id}' introduces a circular dependency cycle")

    def get_task(self, task_id: str) -> Optional[TaskNode]:
        return self._tasks.get(task_id)

    def all_tasks(self) -> List[TaskNode]:
        return list(self._tasks.values())

    def has_cycle(self) -> bool:
        visited: Set[str] = set()
        rec_stack: Set[str] = set()

        def dfs(node_id: str) -> bool:
            visited.add(node_id)
            rec_stack.add(node_id)

            task = self._tasks.get(node_id)
            if task:
                for dep in task.dependencies:
                    if dep not in self._tasks:
                        continue
                    if dep not in visited:
                        if dfs(dep):
                            return True
                    elif dep in rec_stack:
                        return True

            rec_stack.remove(node_id)
            return False

        for node_id in self._tasks:
            if node_id not in visited:
                if dfs(node_id):
                    return True
        return False

    def ready_items(self) -> List[TaskNode]:
        """Returns items in READY state whose dependencies are completely satisfied (all DONE)."""
        ready = []
        for task in self._tasks.values():
            if task.status != TaskStatus.READY:
                continue

            deps_satisfied = True
            for dep_id in task.dependencies:
                dep_task = self._tasks.get(dep_id)
                if not dep_task or dep_task.status != TaskStatus.DONE:
                    deps_satisfied = False
                    break

            if deps_satisfied:
                ready.append(task)
        return ready

    def lock(self, task_id: str, agent_id: str) -> bool:
        task = self._tasks.get(task_id)
        if not task or task.status != TaskStatus.READY:
            return False

        now = datetime.now(timezone.utc)
        task.status = TaskStatus.LOCKED
        task.assigned_agent = agent_id
        task.locked_at = now
        task.last_heartbeat = now
        return True

    def heartbeat(self, task_id: str) -> bool:
        task = self._tasks.get(task_id)
        if not task or task.status != TaskStatus.LOCKED:
            return False
        task.last_heartbeat = datetime.now(timezone.utc)
        return True

    def stage(self, task_id: str) -> bool:
        task = self._tasks.get(task_id)
        if not task or task.status != TaskStatus.LOCKED:
            return False
        task.status = TaskStatus.STAGED
        return True

    def complete(self, task_id: str) -> bool:
        task = self._tasks.get(task_id)
        if not task or task.status not in (TaskStatus.LOCKED, TaskStatus.STAGED):
            return False
        task.status = TaskStatus.DONE
        task.completed_at = datetime.now(timezone.utc)
        return True

    def fail(self, task_id: str, reason: str) -> bool:
        task = self._tasks.get(task_id)
        if not task:
            return False
        task.status = TaskStatus.FAILED
        task.failure_reason = reason
        return True

    def expire_stale_locks(self, timeout_seconds: float = 600.0) -> List[str]:
        """Resets any locked tasks whose heartbeat has exceeded timeout back to READY."""
        expired = []
        now = datetime.now(timezone.utc)

        for task in self._tasks.values():
            if task.status != TaskStatus.LOCKED or not task.last_heartbeat:
                continue

            delta = (now - task.last_heartbeat).total_seconds()
            if delta > timeout_seconds:
                task.status = TaskStatus.READY
                task.assigned_agent = None
                task.locked_at = None
                task.last_heartbeat = None
                expired.append(task.id)

        return expired
