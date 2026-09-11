import pytest
from vad.runners.base_adapter import (
    BaseRunnerAdapter,
    BudgetExceededError,
    RunResult,
    RunnerTaskSpec,
)


class DummyRunner(BaseRunnerAdapter):
    def run_task(self, spec: RunnerTaskSpec) -> RunResult:
        tools = self.filter_tools(["git", "curl", "pytest"], allowed=spec.allowed_tools, denied=spec.denied_tools)
        self.record_spend(tokens=1000, cost=0.05)
        return RunResult(
            task_id=spec.task_id,
            runner_name=self.name,
            success=True,
            output="Executed task cleanly",
            tokens_used=1000,
            cost_usd=0.05,
            tools_invoked=tools,
        )


def test_base_adapter_tool_filtering():
    runner = DummyRunner("dummy-runner")
    # Denied takes precedence, allowed filters
    filtered = runner.filter_tools(
        requested_tools=["git", "bash", "curl", "pytest"],
        allowed=["git", "curl", "pytest"],
        denied=["curl"],
    )
    assert "git" in filtered
    assert "pytest" in filtered
    assert "curl" not in filtered
    assert "bash" not in filtered


def test_base_adapter_budget_cap():
    runner = DummyRunner("dummy-capped", max_tokens=2500, max_cost_usd=0.12)
    spec = RunnerTaskSpec(task_id="T-BUDGET", prompt="Run work")

    # Run 1: 1000 tokens, 0.05 cost -> OK
    res1 = runner.run_task(spec)
    assert res1.success is True
    budget = runner.get_budget()
    assert budget.tokens_remaining == 1500

    # Run 2: 2000 tokens, 0.10 cost -> OK
    res2 = runner.run_task(spec)
    assert res2.success is True

    # Run 3: 3000 tokens > 2500 max -> BudgetExceededError
    with pytest.raises(BudgetExceededError):
        runner.run_task(spec)
