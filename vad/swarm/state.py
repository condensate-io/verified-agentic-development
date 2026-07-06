import json
from pathlib import Path

from pydantic import BaseModel, Field

from vad.swarm.tasks import SwarmTaskGraph, SwarmTaskStatus
from vad.swarm.transport import SwarmMessage


class SwarmState(BaseModel):
    run_id: str
    graph: SwarmTaskGraph
    messages: list[SwarmMessage] = Field(default_factory=list)

    def mark_completed(self, task_id: str) -> bool:
        for task in self.graph.tasks:
            if task.task_id == task_id:
                if task.status == SwarmTaskStatus.COMPLETED:
                    return False
                task.status = SwarmTaskStatus.COMPLETED
                return True
        raise ValueError(f"unknown task id: {task_id}")

    def save(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(self.model_dump(mode="json"), indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> "SwarmState":
        return cls(**json.loads(Path(path).read_text(encoding="utf-8")))
