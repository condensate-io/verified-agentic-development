import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from task_service import summarize_status


def test_summarize_status_counts_known_states():
    tasks = [
        {"id": "plan", "status": "active"},
        {"id": "budget", "status": "blocked"},
        {"id": "verify", "status": "passed"},
        {"id": "deploy", "status": "active"},
    ]

    assert summarize_status(tasks) == {
        "active": 2,
        "blocked": 1,
        "passed": 1,
        "unknown": 0,
    }


def test_summarize_status_handles_empty_and_unknown_states():
    assert summarize_status([]) == {
        "active": 0,
        "blocked": 0,
        "passed": 0,
        "unknown": 0,
    }
    assert summarize_status([{"id": "mystery", "status": "waiting"}])["unknown"] == 1
