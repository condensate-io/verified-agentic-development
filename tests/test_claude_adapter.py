import pytest
from vad.runners.adapters.claude_adapter import ClaudeRunnerAdapter
from vad.runners.base_adapter import RunnerTaskSpec


def test_claude_adapter_execution():
    adapter = ClaudeRunnerAdapter(model="claude-3-5-sonnet")
    spec = RunnerTaskSpec(
        task_id="T-CLAUDE",
        prompt="Implement unit tests for auth module",
        allowed_tools=["pytest", "read_file"],
    )

    result = adapter.run_task(spec)
    assert result.success is True
    assert result.runner_name == "claude"
    assert result.tokens_used > 0
    assert result.cost_usd > 0.0
    assert result.tools_invoked == ["read_file", "pytest"]
    assert "Claude-claude-3-5-sonnet" in result.output


def test_claude_adapter_budget_enforcement():
    # Strict budget: max 50 tokens, whereas default client produces >= 100
    adapter = ClaudeRunnerAdapter(max_tokens=50)
    spec = RunnerTaskSpec(
        task_id="T-CLAUDE-CAPPED",
        prompt="Process this prompt",
    )

    res = adapter.run_task(spec)
    assert res.success is False
    assert res.budget_exceeded is True
    assert "Budget cap exceeded" in str(res.error_message)
