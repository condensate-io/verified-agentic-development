"""Claude Runner Adapter: Executes tasks against Anthropic Claude models with strict budget caps."""

from typing import Any, Callable, Dict, List, Optional
from vad.runners.base_adapter import (
    BaseRunnerAdapter,
    BudgetExceededError,
    RunResult,
    RunnerTaskSpec,
)


class ClaudeRunnerAdapter(BaseRunnerAdapter):
    """Adapter for running tasks using Claude models with budget limits and tool filtering."""

    PRICING_PER_1K_TOKENS = {
        "claude-3-5-sonnet": 0.003,
        "claude-3-5-haiku": 0.0008,
        "claude-3-7-sonnet": 0.003,
        "default": 0.003,
    }

    def __init__(
        self,
        model: str = "claude-3-5-sonnet",
        max_tokens: int = 100000,
        max_cost_usd: float = 10.0,
        client_fn: Optional[Callable[[str, List[str]], Dict[str, Any]]] = None,
    ):
        super().__init__(name="claude", max_tokens=max_tokens, max_cost_usd=max_cost_usd)
        self.model = model
        self.client_fn = client_fn or self._default_client_mock

    def _default_client_mock(self, prompt: str, tools: List[str]) -> Dict[str, Any]:
        """Deterministic offline mock when live API credentials are not injected."""
        token_count = max(100, len(prompt.split()) * 3)
        return {
            "output": f"[Claude-{self.model}] Successfully processed task with tools {tools}",
            "tokens_used": token_count,
            "exit_code": 0,
        }

    def run_task(self, spec: RunnerTaskSpec) -> RunResult:
        # 1. Filter tools
        available_tools = ["bash", "read_file", "write_file", "grep", "pytest"]
        allowed_tools = self.filter_tools(
            available_tools,
            allowed=spec.allowed_tools,
            denied=spec.denied_tools,
        )

        # 2. Invoke model client
        try:
            res_data = self.client_fn(spec.prompt, allowed_tools)
            tokens = res_data.get("tokens_used", 200)
            unit_price = self.PRICING_PER_1K_TOKENS.get(self.model, self.PRICING_PER_1K_TOKENS["default"])
            cost = round((tokens / 1000.0) * unit_price, 5)

            # 3. Track and check budget cap
            self.record_spend(tokens=tokens, cost=cost)

            return RunResult(
                task_id=spec.task_id,
                runner_name=self.name,
                success=True,
                output=res_data.get("output", ""),
                tokens_used=tokens,
                cost_usd=cost,
                tools_invoked=allowed_tools,
                exit_code=res_data.get("exit_code", 0),
            )
        except BudgetExceededError as e:
            return RunResult(
                task_id=spec.task_id,
                runner_name=self.name,
                success=False,
                output="",
                budget_exceeded=True,
                error_message=str(e),
                exit_code=137,
            )
        except Exception as e:
            return RunResult(
                task_id=spec.task_id,
                runner_name=self.name,
                success=False,
                output="",
                error_message=str(e),
                exit_code=1,
            )
