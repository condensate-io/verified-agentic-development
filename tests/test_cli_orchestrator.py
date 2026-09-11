import pytest
from vad.orchestrator.cli_orchestrator import CLIOrchestrator, TrackerMarkdownSync, main
from vad.orchestrator.dep_graph import TaskStatus


@pytest.fixture
def temp_tracker(tmp_path):
    tracker_file = tmp_path / "implementation_tracker.md"
    tracker_file.write_text(
        """
# Test Tracker

## Phase A

| ID | Status | Agent | Locked At | Heartbeat | Completed At | Notes |
|----|--------|-------|-----------|-----------|--------------|-------|
| T1 | done   | ag-1  | 10:00     | 10:05     | 2026-09-11   | Verified |
| T2 | ready  | —     | —         | —         | —            | Depends on T1 |
| T3 | locked | ag-2  | 10:00     | 10:00     | —            | Crashed worker |
"""
    )
    return str(tracker_file)


def test_tracker_markdown_parsing(temp_tracker):
    tasks = TrackerMarkdownSync.parse_tasks(temp_tracker)
    assert len(tasks) == 3

    t1 = next(t for t in tasks if t.id == "T1")
    assert t1.status == TaskStatus.DONE

    t2 = next(t for t in tasks if t.id == "T2")
    assert t2.status == TaskStatus.READY
    assert "T1" in t2.dependencies


def test_tracker_markdown_update_status(temp_tracker):
    success = TrackerMarkdownSync.update_task_status(temp_tracker, "T2", "locked", agent="worker-9")
    assert success is True

    tasks = TrackerMarkdownSync.parse_tasks(temp_tracker)
    t2 = next(t for t in tasks if t.id == "T2")
    assert t2.status == TaskStatus.LOCKED


def test_cli_orchestrator_status_and_run(temp_tracker):
    orch = CLIOrchestrator(tracker_path=temp_tracker)
    summary = orch.get_status_summary()
    assert summary["done"] == 1
    assert summary["ready"] == 1
    assert summary["locked"] == 1

    # Running main CLI entrypoint with status
    exit_code = main(["status", "--tracker", temp_tracker])
    assert exit_code == 0
