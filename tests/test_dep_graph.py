from datetime import datetime, timedelta, timezone
import pytest
from vad.orchestrator.dep_graph import DependencyGraph, TaskNode, TaskStatus


def test_dep_graph_dependency_satisfaction():
    graph = DependencyGraph()
    t1 = TaskNode(id="T1", title="Base Schema")
    t2 = TaskNode(id="T2", title="Database Migration", dependencies=["T1"])
    t3 = TaskNode(id="T3", title="API Endpoint", dependencies=["T2"])

    graph.add_task(t1)
    graph.add_task(t2)
    graph.add_task(t3)

    # Only T1 has no dependencies
    ready = graph.ready_items()
    assert len(ready) == 1
    assert ready[0].id == "T1"

    # Lock and complete T1
    assert graph.lock("T1", "agent-1") is True
    assert graph.complete("T1") is True

    # Now T2 should be ready
    ready = graph.ready_items()
    assert len(ready) == 1
    assert ready[0].id == "T2"


def test_dep_graph_cycle_detection():
    graph = DependencyGraph()
    t1 = TaskNode(id="A", title="Node A", dependencies=["B"])
    t2 = TaskNode(id="B", title="Node B", dependencies=["C"])
    t3 = TaskNode(id="C", title="Node C", dependencies=["A"])

    graph.add_task(t1)
    graph.add_task(t2)
    with pytest.raises(ValueError, match="circular dependency cycle"):
        graph.add_task(t3)


def test_dep_graph_stale_lock_expiry():
    graph = DependencyGraph()
    t1 = TaskNode(id="T1", title="Task 1")
    graph.add_task(t1)

    graph.lock("T1", "crashed-agent")
    task = graph.get_task("T1")
    assert task.status == TaskStatus.LOCKED

    # Simulate heartbeat older than 15 minutes
    task.last_heartbeat = datetime.now(timezone.utc) - timedelta(minutes=15)

    # Expiry with 10 minute timeout (600s)
    expired = graph.expire_stale_locks(timeout_seconds=600)
    assert "T1" in expired
    assert task.status == TaskStatus.READY
    assert task.assigned_agent is None
