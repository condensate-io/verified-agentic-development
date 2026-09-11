"""Codex / OpenAI Runner Adapter: Executes tasks against OpenAI models with budget enforcement."""

from typing import Any, Callable, Dict, List, Optional
from vad.runners.base_adapter import (
    BaseRunnerAdapter,
    BudgetExceededError,
    RunResult,
    RunnerTaskSpec,
)


class CodexRunnerAdapter(BaseRunnerAdapter):
    """Adapter for executing tasks via OpenAI / Codex models."""

    def __init__(
        self,
        model: str = "gpt-4o",
        max_tokens: int = 100000,
        max_cost_usd: float = 10.0,
        client_fn: Optional[Callable[[str, List[str]], Dict[str, Any]]] = None,
    ):
        super().__init__(name="codex", max_tokens=max_tokens, max_cost_usd=max_cost_usd)
        self.model = model
        self.client_fn = client_fn or self._default_client

    def _default_client(self, prompt: str, tools: List[str]) -> Dict[str, Any]:
        tokens = max(100, len(prompt.split()) * 3)
        return {
            "output": f"[Codex-{self.model}] Code generation output for: {prompt[:40]}...",
            "tokens_used": tokens,
            "exit_code": 0,
        }

    def run_task(self, spec: RunnerTaskSpec) -> RunResult:
        available_tools = ["file_read", "file_write", "terminal_run", "git_cmd"]
        allowed = self.filter_tools(available_tools, allowed=spec.allowed_tools, denied=spec.denied_tools)

        try:
            res_data = self.client_fn(spec.prompt, allowed)
            tokens = res_data.get("tokens_used", 150)
            cost = round((tokens / 1000.0) * 0.005, 5)

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
