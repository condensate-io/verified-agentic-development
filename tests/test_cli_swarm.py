import json
import sys
from unittest.mock import patch

from vad.cli import main


def test_cli_swarm_run_completes_local_distributed_fixture(tmp_path):
    state_file = tmp_path / "swarm-state.json"
    out_file = tmp_path / "swarm-run.json"

    with patch.object(sys, "argv", [
        "vad", "swarm", "run", "--run-id", "run-1", "--state", str(state_file), "--out", str(out_file),
    ]):
        main()

    payload = json.loads(out_file.read_text(encoding="utf-8"))
    assert payload["final_decision"] == "passed"
    assert payload["completed_task_ids"] == ["plan", "build", "verify", "audit"]
    assert state_file.exists()


def test_cli_swarm_status_reconstructs_task_graph_and_final_decision(tmp_path):
    state_file = tmp_path / "swarm-state.json"
    status_file = tmp_path / "swarm-status.json"

    with patch.object(sys, "argv", ["vad", "swarm", "run", "--run-id", "run-1", "--state", str(state_file)]):
        main()
    with patch.object(sys, "argv", ["vad", "swarm", "status", str(state_file), "--out", str(status_file)]):
        main()

    payload = json.loads(status_file.read_text(encoding="utf-8"))
    assert payload["final_decision"] == "passed"
    assert [task["task_id"] for task in payload["tasks"]] == ["plan", "build", "verify", "audit"]
    assert [message["message_type"] for message in payload["messages"]] == [
        "task_planned",
        "build_completed",
        "verification_completed",
        "audit_completed",
    ]
