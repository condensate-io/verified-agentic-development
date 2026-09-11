import pytest
from vad.runners.adapters.codex_adapter import CodexRunnerAdapter
from vad.runners.base_adapter import RunnerTaskSpec


def test_codex_adapter_execution():
    adapter = CodexRunnerAdapter(model="gpt-4o")
    spec = RunnerTaskSpec(
        task_id="T-CODEX-1",
        prompt="Write a distributed lock implementation using Redis",
        allowed_tools=["file_write", "terminal_run"],
    )

    result = adapter.run_task(spec)
    assert result.success is True
    assert result.runner_name == "codex"
    assert "Codex-gpt-4o" in result.output
    assert "file_write" in result.tools_invoked
    assert result.tokens_used > 0


def test_codex_adapter_budget_cap():
    adapter = CodexRunnerAdapter(max_tokens=50)
    spec = RunnerTaskSpec(
        task_id="T-CODEX-CAP",
        prompt="Write extensive distributed system code exceeding tokens",
    )

    res = adapter.run_task(spec)
    assert res.success is False
    assert res.budget_exceeded is True
