"""Task Admission Controller: Governs entry into the DAG via duplicate guards, cycle checks, and DoR gates."""

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field
from vad.contracts.models import EIP
from vad.decision.dor_gate import DoREvaluationResult, DoRGate, DoRStatus
from vad.orchestrator.dep_graph import DependencyGraph, TaskNode


class TaskAdmissionRequest(BaseModel):
    task_id: str
    title: str
    dependencies: List[str] = Field(default_factory=list)
    eip: Optional[EIP] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class TaskAdmissionResult(BaseModel):
    task_id: str
    admitted: bool
    status: str
    reasons: List[str] = Field(default_factory=list)
    dor_result: Optional[DoREvaluationResult] = None


class TaskAdmissionController:
    """Validates new task admission requests before incorporating them into the execution DAG."""

    def __init__(self, graph: DependencyGraph, dor_gate: Optional[DoRGate] = None):
        self.graph = graph
        self.dor_gate = dor_gate or DoRGate()

    def admit(self, request: TaskAdmissionRequest) -> TaskAdmissionResult:
        reasons = []

        # 1. Duplicate ID check
        if self.graph.get_task(request.task_id) is not None:
            return TaskAdmissionResult(
                task_id=request.task_id,
                admitted=False,
                status="rejected",
                reasons=[f"Task ID '{request.task_id}' already exists in execution graph"],
            )

        # 2. DoR Gate check if EIP is attached
        dor_res = None
        if request.eip:
            dor_res = self.dor_gate.evaluate(request.eip)
            if not dor_res.admitted:
                return TaskAdmissionResult(
                    task_id=request.task_id,
                    admitted=False,
                    status=dor_res.status.value,
                    reasons=[f"DoR gate failed: {dor_res.reason}"] + dor_res.missing_criteria,
                    dor_result=dor_res,
                )

        # 3. Cycle and dependency validity check
        task_node = TaskNode(
            id=request.task_id,
            title=request.title,
            dependencies=request.dependencies,
            metadata=request.metadata,
        )
        try:
            self.graph.add_task(task_node)
        except ValueError as e:
            return TaskAdmissionResult(
                task_id=request.task_id,
                admitted=False,
                status="rejected",
                reasons=[str(e)],
                dor_result=dor_res,
            )

        return TaskAdmissionResult(
            task_id=request.task_id,
            admitted=True,
            status="admitted",
            reasons=["Task successfully admitted to execution DAG"],
            dor_result=dor_res,
        )
