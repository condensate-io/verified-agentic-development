import pytest
from vad.runners.adapters.copilot_adapter import CopilotRunnerAdapter
from vad.runners.base_adapter import RunnerTaskSpec


def test_copilot_adapter_execution():
    adapter = CopilotRunnerAdapter()
    spec = RunnerTaskSpec(
        task_id="T-COPILOT-1",
        prompt="Generate FastAPI telemetry router",
        allowed_tools=["code_edit"],
    )

    result = adapter.run_task(spec)
    assert result.success is True
    assert result.runner_name == "copilot"
    assert "GitHub Copilot" in result.output
    assert result.tools_invoked == ["code_edit"]
    assert result.tokens_used > 0


def test_copilot_adapter_budget_cap():
    adapter = CopilotRunnerAdapter(max_tokens=40)
    spec = RunnerTaskSpec(
        task_id="T-COPILOT-CAP",
        prompt="A prompt needing tokens beyond limit",
    )

    res = adapter.run_task(spec)
    assert res.success is False
    assert res.budget_exceeded is True
