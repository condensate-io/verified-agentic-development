"""SDK Runner: Unified registry and execution coordinator across all agent runner adapters."""

from typing import Dict, List, Optional
from vad.runners.adapters.aider_adapter import AiderRunnerAdapter
from vad.runners.adapters.claude_adapter import ClaudeRunnerAdapter
from vad.runners.adapters.codex_adapter import CodexRunnerAdapter
from vad.runners.adapters.copilot_adapter import CopilotRunnerAdapter
from vad.runners.adapters.cursor_adapter import CursorRunnerAdapter
from vad.runners.base_adapter import BaseRunnerAdapter, RunResult, RunnerTaskSpec


class RunnerRegistry:
    """Registry maintaining initialized runner adapter instances."""

    def __init__(self):
        self._adapters: Dict[str, BaseRunnerAdapter] = {}

    def register(self, adapter: BaseRunnerAdapter) -> None:
        self._adapters[adapter.name.lower()] = adapter

    def get(self, name: str) -> Optional[BaseRunnerAdapter]:
        return self._adapters.get(name.lower())

    def list_all(self) -> List[str]:
        return list(self._adapters.keys())

    @classmethod
    def default_registry(cls) -> "RunnerRegistry":
        reg = cls()
        reg.register(ClaudeRunnerAdapter())
        reg.register(CopilotRunnerAdapter())
        reg.register(CursorRunnerAdapter())
        reg.register(CodexRunnerAdapter())
        reg.register(AiderRunnerAdapter())
        return reg


class SDKRunner:
    """Orchestrates task execution through selected agent runners with retry logic and tool policies."""

    def __init__(
        self,
        registry: Optional[RunnerRegistry] = None,
        default_runner: str = "claude",
        max_retries: int = 2,
    ):
        self.registry = registry or RunnerRegistry.default_registry()
        self.default_runner = default_runner
        self.max_retries = max_retries

    def execute(
        self,
        spec: RunnerTaskSpec,
        runner_name: Optional[str] = None,
        tool_policy: Optional[List[str]] = None,
    ) -> RunResult:
        target_name = (runner_name or self.default_runner).lower()
        adapter = self.registry.get(target_name)
        if not adapter:
            adapter = self.registry.get(self.default_runner)
            if not adapter:
                return RunResult(
                    task_id=spec.task_id,
                    runner_name=target_name,
                    success=False,
                    output="",
                    error_message=f"Runner '{target_name}' not found in registry and default failed.",
                    exit_code=1,
                )

        # Apply tool policy override if specified
        if tool_policy is not None:
            spec.allowed_tools = tool_policy

        # Retry loop for transient failures
        last_result: Optional[RunResult] = None
        for attempt in range(self.max_retries + 1):
            result = adapter.run_task(spec)
            if result.success:
                return result
            # If budget exceeded, do not retry
            if result.budget_exceeded:
                return result
            last_result = result

        return last_result or RunResult(
            task_id=spec.task_id,
            runner_name=adapter.name,
            success=False,
            output="",
            error_message="Execution failed after retries",
            exit_code=1,
        )
