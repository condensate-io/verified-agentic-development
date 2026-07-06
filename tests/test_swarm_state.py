from vad.swarm.agents import AgentRole
from vad.swarm.state import SwarmState
from vad.swarm.tasks import SwarmTask, SwarmTaskGraph, SwarmTaskStatus


def make_state():
    return SwarmState(
        run_id="run-1",
        graph=SwarmTaskGraph(tasks=[
            SwarmTask(task_id="plan", role=AgentRole.PLANNER, description="plan"),
            SwarmTask(task_id="build", role=AgentRole.BUILDER, description="build", depends_on=["plan"]),
        ]),
    )


def test_interrupted_swarm_run_can_resume_from_persisted_state(tmp_path):
    state = make_state()
    assert state.mark_completed("plan") is True
    state_file = tmp_path / "swarm-state.json"

    state.save(state_file)
    reloaded = SwarmState.load(state_file)

    assert reloaded.run_id == "run-1"
    assert reloaded.graph.tasks[0].status == SwarmTaskStatus.COMPLETED
    assert [task.task_id for task in reloaded.graph.ready_tasks()] == ["build"]


def test_duplicate_task_completion_is_idempotent():
    state = make_state()

    first = state.mark_completed("plan")
    second = state.mark_completed("plan")

    assert first is True
    assert second is False
    assert state.graph.tasks[0].status == SwarmTaskStatus.COMPLETED
