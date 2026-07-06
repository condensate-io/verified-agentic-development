import json
import sys
from pathlib import Path
from unittest.mock import patch

from vad.cli import main
from vad.swarm.demo import run_level3_demo_swarm
from vad.swarm.state import SwarmState


FIXTURE = Path("examples/level3-demo")


def test_level3_demo_swarm_modifies_copied_repo_and_verifies(tmp_path):
    state_path = tmp_path / "swarm-state.json"

    result = run_level3_demo_swarm(
        "level3-demo-swarm",
        FIXTURE,
        tmp_path / "work",
        state_path,
    )

    assert result.final_decision == "passed"
    assert result.completed_task_ids == ["plan", "build", "verify", "audit"]
    assert result.verification["passed"] is True
    assert len(result.modified_files) == 1
    assert Path(result.modified_files[0]).read_text(encoding="utf-8").startswith("# Status Summary Report")
    assert set(result.agent_roles) == {"planner", "builder", "verifier", "auditor"}

    state = SwarmState.load(state_path)
    assert [message.sender_role.value for message in state.messages] == ["planner", "builder", "verifier", "auditor"]


def test_cli_swarm_run_uses_level3_fixture(tmp_path):
    out_file = tmp_path / "swarm-run.json"
    state_file = tmp_path / "swarm-state.json"

    with patch.object(sys, "argv", [
        "vad",
        "swarm",
        "run",
        "--run-id",
        "level3-demo-swarm",
        "--fixture",
        str(FIXTURE),
        "--workdir",
        str(tmp_path / "work"),
        "--state",
        str(state_file),
        "--out",
        str(out_file),
    ]):
        main()

    payload = json.loads(out_file.read_text(encoding="utf-8"))

    assert payload["final_decision"] == "passed"
    assert payload["verification"]["passed"] is True
    assert payload["completed_task_ids"] == ["plan", "build", "verify", "audit"]
    assert set(payload["agent_roles"]) == {"planner", "builder", "verifier", "auditor"}
    assert state_file.exists()
