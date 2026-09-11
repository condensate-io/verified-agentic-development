import pytest
from vad.runners.adapters.aider_adapter import AiderRunnerAdapter
from vad.runners.base_adapter import RunnerTaskSpec


def test_aider_adapter_execution():
    adapter = AiderRunnerAdapter()
    spec = RunnerTaskSpec(
        task_id="T-AIDER-1",
        prompt="Fix regression in retry logic and add test coverage",
        allowed_tools=["git_diff", "file_edit"],
    )

    result = adapter.run_task(spec)
    assert result.success is True
    assert result.runner_name == "aider"
    assert "Aider-Diff" in result.output
    assert "git_diff" in result.tools_invoked
    assert result.tokens_used > 0


def test_aider_adapter_budget_cap():
    adapter = AiderRunnerAdapter(max_tokens=40)
    spec = RunnerTaskSpec(
        task_id="T-AIDER-CAP",
        prompt="Execute large multi-file diff refactoring",
    )

    res = adapter.run_task(spec)
    assert res.success is False
    assert res.budget_exceeded is True
