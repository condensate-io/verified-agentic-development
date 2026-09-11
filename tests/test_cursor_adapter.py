import pytest
from vad.runners.adapters.cursor_adapter import CursorRunnerAdapter
from vad.runners.base_adapter import RunnerTaskSpec


def test_cursor_adapter_execution():
    adapter = CursorRunnerAdapter()
    spec = RunnerTaskSpec(
        task_id="T-CURSOR-1",
        prompt="Refactor database connection pool timeout logic",
        allowed_tools=["file_editor", "terminal_command"],
    )

    result = adapter.run_task(spec)
    assert result.success is True
    assert result.runner_name == "cursor"
    assert "Cursor-Agent" in result.output
    assert "file_editor" in result.tools_invoked
    assert result.tokens_used > 0


def test_cursor_adapter_budget_cap():
    adapter = CursorRunnerAdapter(max_tokens=50)
    spec = RunnerTaskSpec(
        task_id="T-CURSOR-CAP",
        prompt="Refactor entire repository schema across multiple directories",
    )

    res = adapter.run_task(spec)
    assert res.success is False
    assert res.budget_exceeded is True
