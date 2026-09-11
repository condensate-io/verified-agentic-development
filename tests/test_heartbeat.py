from datetime import datetime, timedelta, timezone
import pytest
from vad.orchestrator.dep_graph import DependencyGraph, TaskNode, TaskStatus
from vad.orchestrator.heartbeat import HeartbeatMonitor


def test_heartbeat_ping_updates_timestamp():
    graph = DependencyGraph()
    graph.add_task(TaskNode(id="HB-1", title="Heartbeat Task"))
    graph.lock("HB-1", "agent-x")

    monitor = HeartbeatMonitor(graph, timeout_seconds=10.0)
    assert monitor.ping("HB-1", "agent-x") is True

    record = monitor.get_record("HB-1")
    assert record is not None
    assert record.ping_count == 1

    # Second ping increments count
    assert monitor.ping("HB-1", "agent-x") is True
    assert record.ping_count == 2


def test_heartbeat_timeout_resets_crashed_task():
    graph = DependencyGraph()
    graph.add_task(TaskNode(id="HB-CRASH", title="Crashing Task"))
    graph.lock("HB-CRASH", "crashed-subagent")

    monitor = HeartbeatMonitor(graph, timeout_seconds=5.0)
    monitor.ping("HB-CRASH", "crashed-subagent")

    # Simulate past timestamp
    record = monitor.get_record("HB-CRASH")
    record.last_ping = datetime.now(timezone.utc) - timedelta(seconds=10)

    task = graph.get_task("HB-CRASH")
    task.last_heartbeat = datetime.now(timezone.utc) - timedelta(seconds=10)

    expired = monitor.check_all()
    assert "HB-CRASH" in expired
    assert task.status == TaskStatus.READY
    assert task.assigned_agent is None
