"""Base Runner Adapter: Defines common contract, tool filtering, and budget caps for all AI agent runners."""

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class BudgetExceededError(Exception):
    """Raised when token or cost budget cap is exceeded."""
    pass


class RunnerTaskSpec(BaseModel):
    task_id: str
    prompt: str
    workspace_dir: str = "/workspace"
    allowed_tools: List[str] = Field(default_factory=list)
    denied_tools: List[str] = Field(default_factory=list)
    max_tokens: int = 10000
    max_cost_usd: float = 2.0
    timeout_seconds: float = 120.0
    metadata: Dict[str, Any] = Field(default_factory=dict)


class RunResult(BaseModel):
    task_id: str
    runner_name: str
    success: bool
    output: str
    tokens_used: int = 0
    cost_usd: float = 0.0
    tools_invoked: List[str] = Field(default_factory=list)
    exit_code: int = 0
    budget_exceeded: bool = False
    error_message: Optional[str] = None


class RunnerBudget(BaseModel):
    tokens_remaining: int
    cost_remaining_usd: float
    is_exhausted: bool = False


class BaseRunnerAdapter(ABC):
    """Abstract base class for all agent runners (Claude, Copilot, Cursor, Codex, Aider)."""

    def __init__(self, name: str, max_tokens: int = 100000, max_cost_usd: float = 10.0):
        self.name = name
        self.max_tokens = max_tokens
        self.max_cost_usd = max_cost_usd
        self.tokens_used_total = 0
        self.cost_used_total = 0.0

    def filter_tools(
        self, requested_tools: List[str], allowed: Optional[List[str]] = None, denied: Optional[List[str]] = None
    ) -> List[str]:
        """Enforces tool permissions: drops denied tools, restricts to allowed tools if configured."""
        denied_set = set(denied or [])
        allowed_set = set(allowed) if allowed else None

        filtered = []
        for tool in requested_tools:
            if tool in denied_set:
                continue
            if allowed_set is not None and tool not in allowed_set:
                continue
            filtered.append(tool)
        return filtered

    def get_budget(self) -> RunnerBudget:
        rem_tokens = max(0, self.max_tokens - self.tokens_used_total)
        rem_cost = max(0.0, round(self.max_cost_usd - self.cost_used_total, 4))
        return RunnerBudget(
            tokens_remaining=rem_tokens,
            cost_remaining_usd=rem_cost,
            is_exhausted=(rem_tokens <= 0 or rem_cost <= 0.0),
        )

    def record_spend(self, tokens: int, cost: float) -> None:
        self.tokens_used_total += tokens
        self.cost_used_total += cost
        if self.tokens_used_total > self.max_tokens or self.cost_used_total > self.max_cost_usd:
            raise BudgetExceededError(
                f"Budget cap exceeded for runner '{self.name}': "
                f"tokens={self.tokens_used_total}/{self.max_tokens}, "
                f"cost=${self.cost_used_total:.4f}/${self.max_cost_usd:.4f}"
            )

    @abstractmethod
    def run_task(self, spec: RunnerTaskSpec) -> RunResult:
        """Executes the task specification within the runner's environment."""
        pass
