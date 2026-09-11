import pytest
from vad.contracts.models import (
    EIP,
    Goal,
    ProofObligation,
    ProofKind,
    Invariants,
    Constraints,
    ToolPermissions,
    ModelBudget,
    ReleaseRequirements,
    TelemetryRequirements,
    RiskTier,
    AutonomyTier,
)
from vad.orchestrator.admission import TaskAdmissionController, TaskAdmissionRequest
from vad.orchestrator.dep_graph import DependencyGraph, TaskNode


def make_valid_eip():
    return EIP(
        version="1.0.0",
        name="test-admit-eip",
        goal=Goal(description="Admit task into orchestrator", success_criteria=["Criteria 1"]),
        non_goals=["No unverified merges"],
        risk_tier=RiskTier.LOW,
        autonomy_tier=AutonomyTier.BOUNDED,
        scope_boundaries=["orchestrator"],
        invariants=Invariants(functional=["DAG integrity"]),
        constraints=Constraints(),
        proof_obligations=[ProofObligation(id="p1", kind=ProofKind.UNIT, description="test")],
        tool_permissions=ToolPermissions(allowed=["pytest"]),
        memory_requirements=[],
        model_budget=ModelBudget(max_tokens=1000, max_cost=1.0, max_loop_depth=2),
        release_requirements=ReleaseRequirements(required=False),
        telemetry_requirements=TelemetryRequirements(required=False),
    )


def test_admission_controller_admits_valid_task():
    graph = DependencyGraph()
    controller = TaskAdmissionController(graph)
    req = TaskAdmissionRequest(
        task_id="TASK-1",
        title="Valid Initial Task",
        dependencies=[],
        eip=make_valid_eip(),
    )
    res = controller.admit(req)
    assert res.admitted is True
    assert res.status == "admitted"
    assert graph.get_task("TASK-1") is not None


def test_admission_controller_rejects_duplicate():
    graph = DependencyGraph()
    graph.add_task(TaskNode(id="TASK-DUP", title="Existing"))
    controller = TaskAdmissionController(graph)

    req = TaskAdmissionRequest(task_id="TASK-DUP", title="Duplicate")
    res = controller.admit(req)
    assert res.admitted is False
    assert any("already exists" in r for r in res.reasons)


def test_admission_controller_rejects_on_dor_failure():
    graph = DependencyGraph()
    controller = TaskAdmissionController(graph)

    invalid_eip = make_valid_eip()
    invalid_eip.goal.success_criteria = []  # Violates DoR gate

    req = TaskAdmissionRequest(task_id="TASK-FAIL-DOR", title="Failing DoR", eip=invalid_eip)
    res = controller.admit(req)
    assert res.admitted is False
    assert res.status == "reject"
    assert any("DoR gate failed" in r for r in res.reasons)
