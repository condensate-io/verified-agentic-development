"""Cursor Runner Adapter: Executes tasks using headless Cursor CLI interface."""

from typing import Any, Callable, Dict, List, Optional
from vad.runners.base_adapter import (
    BaseRunnerAdapter,
    BudgetExceededError,
    RunResult,
    RunnerTaskSpec,
)


class CursorRunnerAdapter(BaseRunnerAdapter):
    """Adapter for executing tasks through Cursor in headless / CLI mode."""

    def __init__(
        self,
        cli_bin: str = "cursor",
        max_tokens: int = 100000,
        max_cost_usd: float = 10.0,
        invoker_fn: Optional[Callable[[str, List[str]], Dict[str, Any]]] = None,
    ):
        super().__init__(name="cursor", max_tokens=max_tokens, max_cost_usd=max_cost_usd)
        self.cli_bin = cli_bin
        self.invoker_fn = invoker_fn or self._default_invoker

    def _default_invoker(self, prompt: str, tools: List[str]) -> Dict[str, Any]:
        tokens = max(100, len(prompt.split()) * 3)
        return {
            "output": f"[Cursor-Agent] Modified workspace files according to: {prompt[:40]}...",
            "tokens_used": tokens,
            "exit_code": 0,
        }

    def run_task(self, spec: RunnerTaskSpec) -> RunResult:
        available_tools = ["file_editor", "codebase_index", "terminal_command"]
        allowed = self.filter_tools(available_tools, allowed=spec.allowed_tools, denied=spec.denied_tools)

        try:
            res_data = self.invoker_fn(spec.prompt, allowed)
            tokens = res_data.get("tokens_used", 150)
            cost = round((tokens / 1000.0) * 0.0025, 5)

            self.record_spend(tokens=tokens, cost=cost)

            return RunResult(
                task_id=spec.task_id,
                runner_name=self.name,
                success=True,
                output=res_data.get("output", ""),
                tokens_used=tokens,
                cost_usd=cost,
                tools_invoked=allowed,
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
