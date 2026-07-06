from enum import Enum

from pydantic import BaseModel, Field, model_validator

from vad.swarm.agents import AgentRole


class SwarmTaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    BLOCKED = "blocked"


class SwarmTask(BaseModel):
    task_id: str = Field(min_length=1)
    role: AgentRole
    description: str = Field(min_length=1)
    depends_on: list[str] = Field(default_factory=list)
    status: SwarmTaskStatus = SwarmTaskStatus.PENDING


class SwarmTaskGraph(BaseModel):
    tasks: list[SwarmTask] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_graph(self):
        ids = [task.task_id for task in self.tasks]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate task ids are not allowed")
        task_ids = set(ids)
        for task in self.tasks:
            missing = set(task.depends_on) - task_ids
            if missing:
                raise ValueError(f"unknown task dependencies for {task.task_id}: {', '.join(sorted(missing))}")
        _reject_cycles({task.task_id: task.depends_on for task in self.tasks})
        return self

    def ready_tasks(self) -> list[SwarmTask]:
        completed = {task.task_id for task in self.tasks if task.status == SwarmTaskStatus.COMPLETED}
        return [
            task for task in self.tasks
            if task.status == SwarmTaskStatus.PENDING and set(task.depends_on).issubset(completed)
        ]


def _reject_cycles(edges: dict[str, list[str]]) -> None:
    visiting = set()
    visited = set()

    def visit(task_id: str):
        if task_id in visiting:
            raise ValueError(f"cycle detected at task {task_id}")
        if task_id in visited:
            return
        visiting.add(task_id)
        for dependency in edges[task_id]:
            visit(dependency)
        visiting.remove(task_id)
        visited.add(task_id)

    for task_id in edges:
        visit(task_id)
