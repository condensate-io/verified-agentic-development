import sqlite3
import pytest
from pathlib import Path
from vad.orchestrator.circuit_breaker import CircuitBreaker
from vad.orchestrator.dep_graph import DependencyGraph, TaskNode, TaskStatus
from vad.orchestrator.push_gate import PushGate
from vad.plugins.claude_code.mcp_integration import VADMCPServer
from vad.server.db.store import RunRecord, ServerStore


def test_agent_cannot_self_report_pushing_mr_created_or_done():
    graph = DependencyGraph()
    task = TaskNode(id="T-1", title="Feature Work", status=TaskStatus.LOCKED)
    graph.add_task(task)

    # 1. Agent attempts to report pushing -> PermissionError
    with pytest.raises(PermissionError) as exc_push:
        graph.report_pushing("T-1", actor_role="agent")
    assert "prohibited from self-reporting pushing" in str(exc_push.value)

    # 2. Agent attempts to report mr_created -> PermissionError
    with pytest.raises(PermissionError) as exc_mr:
        graph.report_mr_created("T-1", actor_role="builder")
    assert "prohibited from self-reporting mr_created" in str(exc_mr.value)

    # 3. Agent attempts to report done -> PermissionError
    with pytest.raises(PermissionError) as exc_done:
        graph.complete("T-1", actor_role="runner")
    assert "prohibited from self-reporting done" in str(exc_done.value)

    # Human/operator can report
    assert graph.report_pushing("T-1", actor_role="operator") is True
    assert graph.report_mr_created("T-1", actor_role="operator") is True
    assert graph.complete("T-1", actor_role="operator") is True
    assert graph.get_task("T-1").status == TaskStatus.DONE


def test_push_gate_and_circuit_breaker_tripping():
    breaker = CircuitBreaker(threshold=3)

    # Mock runner simulating git ls-remote and reflog failures
    def mock_runner_fail_reflog(cmd, **kwargs):
        class Res:
            returncode = 0
            stdout = "abc1111\nabc2222\n"  # does not contain target sha
            stderr = ""
        return Res()

    gate = PushGate(circuit_breaker=breaker, runner=mock_runner_fail_reflog)

    # Violation 1
    res1 = gate.check_staging_readiness("main", "sha-unknown-1")
    assert res1.allowed is False
    assert breaker.violation_count == 1
    assert breaker.is_tripped() is False

    # Violation 2
    res2 = gate.check_staging_readiness("main", "sha-unknown-2")
    assert res2.allowed is False
    assert breaker.violation_count == 2
    assert breaker.is_tripped() is False

    # Violation 3 -> Trips globally!
    res3 = gate.check_staging_readiness("main", "sha-unknown-3")
    assert res3.allowed is False
    assert breaker.violation_count == 3
    assert breaker.is_tripped() is True

    # Attempt 4 is immediately blocked by tripped circuit breaker
    res4 = gate.check_staging_readiness("main", "sha-any")
    assert res4.allowed is False
    assert "Global circuit breaker is TRIPPED" in res4.reason

    # Reset breaker
    breaker.reset("human-operator-alice")
    assert breaker.is_tripped() is False
    assert breaker.violation_count == 0


def test_push_gate_success():
    breaker = CircuitBreaker(threshold=3)
    target_sha = "f0a1b2c3d4e5f600"

    def mock_runner_ok(cmd, **kwargs):
        class Res:
            returncode = 0
            stdout = f"other1\n{target_sha}\nother2"
            stderr = ""
        return Res()

    gate = PushGate(circuit_breaker=breaker, runner=mock_runner_ok)
    res = gate.check_staging_readiness("feature-branch", target_sha)
    assert res.allowed is True
    assert res.reflog_verified is True
    assert breaker.violation_count == 0


def test_server_store_partial_unique_indexes(tmp_path):
    db_file = tmp_path / "test_store.db"
    store = ServerStore(db_file)

    # 1. Create live run on repo "vad-core" with ticket "TICK-100"
    run1 = RunRecord(run_id="run-1", ticket_id="TICK-100", repo="vad-core", status="active")
    store.create_run(run1)

    # 2. Second live run on the SAME ticket should violate partial unique index
    run_dup_ticket = RunRecord(run_id="run-dup-ticket", ticket_id="TICK-100", repo="vad-other", status="active")
    with pytest.raises(sqlite3.IntegrityError):
        store.create_run(run_dup_ticket)

    # 3. Second live run on the SAME repo should violate partial unique index
    run_dup_repo = RunRecord(run_id="run-dup-repo", ticket_id="TICK-200", repo="vad-core", status="active")
    with pytest.raises(sqlite3.IntegrityError):
        store.create_run(run_dup_repo)

    # 4. Once run1 is completed ("done"), a new live run on that ticket and repo IS permitted!
    store.complete_run("run-1", actor_role="operator")
    assert store.load_run("run-1").status == "done"

    run_new = RunRecord(run_id="run-2", ticket_id="TICK-100", repo="vad-core", status="active")
    store.create_run(run_new)
    assert store.load_run("run-2").run_id == "run-2"


def test_investigation_run_kind_requires_approve_rca(tmp_path):
    db_file = tmp_path / "test_store.db"
    store = ServerStore(db_file)

    inv_run = RunRecord(
        run_id="inv-101",
        ticket_id="INC-999",
        repo="voice-service",
        kind="investigation",
        status="active",
        rca_approved=False,
    )
    store.create_run(inv_run)

    # Agent cannot self-report done
    with pytest.raises(PermissionError) as exc_agent:
        store.complete_run("inv-101", actor_role="agent")
    assert "Autonomous agents are prohibited" in str(exc_agent.value)

    # Operator cannot complete before approve_rca
    with pytest.raises(PermissionError) as exc_rca:
        store.complete_run("inv-101", actor_role="operator")
    assert "approve_rca human gate" in str(exc_rca.value)

    # Approve RCA
    store.approve_rca("inv-101", operator_id="operator-bob")
    assert store.load_run("inv-101").rca_approved is True

    # Now operator can complete
    store.complete_run("inv-101", actor_role="operator")
    assert store.load_run("inv-101").status == "done"


def test_confirm_push_not_exposed_to_mcp():
    server = VADMCPServer()
    tool_defs = server.get_tool_definitions()
    tool_names = [t.name for t in tool_defs]

    # confirm_push MUST NOT be in tools list
    assert "confirm_push" not in tool_names
    assert "vad_confirm_push" not in tool_names

    # Calling confirm_push via MCP protocol MUST be blocked
    res = server.call_tool("confirm_push", {})
    assert res.get("isError") is True
    assert "strictly prohibited as an MCP tool" in res["content"][0]["text"]
