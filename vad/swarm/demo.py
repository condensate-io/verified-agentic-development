from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

from pydantic import BaseModel, Field

from vad.swarm.agents import AgentCard, AgentRole
from vad.swarm.coordinator import LocalSwarmCoordinator
from vad.swarm.state import SwarmState
from vad.swarm.tasks import SwarmTask, SwarmTaskGraph


class DemoSwarmRunResult(BaseModel):
    run_id: str
    final_decision: str
    workdir: str
    modified_files: list[str] = Field(default_factory=list)
    completed_task_ids: list[str] = Field(default_factory=list)
    agent_roles: dict[str, list[str]] = Field(default_factory=dict)
    verification: dict = Field(default_factory=dict)
    messages: list[dict] = Field(default_factory=list)


def run_level3_demo_swarm(run_id: str, fixture: Path, workdir: Path, state_path: Path | None = None) -> DemoSwarmRunResult:
    repo_source = fixture / "repo"
    if not repo_source.exists():
        raise FileNotFoundError(f"fixture repo not found: {repo_source}")

    workdir.mkdir(parents=True, exist_ok=True)
    repo_workdir = workdir / "repo"
    if repo_workdir.exists():
        shutil.rmtree(repo_workdir)
    shutil.copytree(repo_source, repo_workdir)

    agents = _demo_agents()
    graph = _demo_graph()
    coordinator = LocalSwarmCoordinator(agents)
    messages = []
    completed = []
    modified_files = []
    verification = {"passed": False, "command": f"{sys.executable} -m pytest tests", "stdout": "", "stderr": ""}

    while len(completed) < len(graph.tasks):
        result = coordinator.run_ready_tasks(graph)
        if not result.completed_task_ids:
            break
        messages.extend(result.messages)
        completed.extend(result.completed_task_ids)
        if "build" in result.completed_task_ids:
            modified_files.append(_write_demo_build_artifact(repo_workdir))
        if "verify" in result.completed_task_ids:
            verification = _run_fixture_tests(repo_workdir)

    state = SwarmState(run_id=run_id, graph=graph, messages=messages)
    if state_path is not None:
        state.save(state_path)

    final_decision = "passed" if verification["passed"] and len(completed) == len(graph.tasks) else "blocked"
    return DemoSwarmRunResult(
        run_id=run_id,
        final_decision=final_decision,
        workdir=str(workdir),
        modified_files=modified_files,
        completed_task_ids=completed,
        agent_roles=_agent_roles(messages),
        verification=verification,
        messages=[message.model_dump(mode="json") for message in messages],
    )


def _demo_agents() -> list[AgentCard]:
    return [
        AgentCard(agent_id="demo-planner", role=AgentRole.PLANNER, capabilities=["plan"], model_tiers=["tier1"], autonomy_limit=1),
        AgentCard(agent_id="demo-builder", role=AgentRole.BUILDER, capabilities=["modify_code"], model_tiers=["tier1"], autonomy_limit=1),
        AgentCard(agent_id="demo-verifier", role=AgentRole.VERIFIER, capabilities=["verify"], model_tiers=["tier1"], autonomy_limit=1),
        AgentCard(agent_id="demo-auditor", role=AgentRole.AUDITOR, capabilities=["audit_evidence"], model_tiers=["tier1"], autonomy_limit=1),
    ]


def _demo_graph() -> SwarmTaskGraph:
    return SwarmTaskGraph(tasks=[
        SwarmTask(task_id="plan", role=AgentRole.PLANNER, description="Plan the Level 3 demo fixture run."),
        SwarmTask(task_id="build", role=AgentRole.BUILDER, description="Modify the copied fixture repo with a deterministic summary artifact.", depends_on=["plan"]),
        SwarmTask(task_id="verify", role=AgentRole.VERIFIER, description="Run the copied fixture repo tests.", depends_on=["build"]),
        SwarmTask(task_id="audit", role=AgentRole.AUDITOR, description="Aggregate swarm evidence by agent role.", depends_on=["verify"]),
    ])


def _write_demo_build_artifact(repo_workdir: Path) -> str:
    report = repo_workdir / "status_summary_report.md"
    report.write_text(
        "# Status Summary Report\n\n"
        "- active: 2\n"
        "- blocked: 1\n"
        "- passed: 1\n"
        "- unknown: 0\n",
        encoding="utf-8",
    )
    return str(report)


def _run_fixture_tests(repo_workdir: Path) -> dict:
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "tests"],
        cwd=repo_workdir,
        text=True,
        capture_output=True,
        check=False,
    )
    return {
        "passed": result.returncode == 0,
        "command": f"{sys.executable} -m pytest tests",
        "stdout": result.stdout,
        "stderr": result.stderr,
    }


def _agent_roles(messages) -> dict[str, list[str]]:
    roles: dict[str, list[str]] = {}
    for message in messages:
        roles.setdefault(message.sender_role.value, []).append(message.task_id)
    return roles
