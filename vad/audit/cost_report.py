"""Cost Report Generator: Calculates transcript-exact costs, USD estimates, and tool counts."""

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from vad.audit.local_audit import AuditEvent, LocalAuditLogger


class ModelPricing(BaseModel):
    input_per_m: float  # USD per 1M prompt/input tokens
    output_per_m: float  # USD per 1M completion/output tokens
    cache_read_per_m: float = 0.0  # USD per 1M cache read tokens
    cache_write_per_m: float = 0.0  # USD per 1M cache write tokens


DEFAULT_MODEL_PRICING: Dict[str, ModelPricing] = {
    # Anthropic
    "claude-3-5-sonnet": ModelPricing(input_per_m=3.00, output_per_m=15.00, cache_read_per_m=0.30, cache_write_per_m=3.75),
    "claude-3-haiku": ModelPricing(input_per_m=0.25, output_per_m=1.25),
    "claude-3-opus": ModelPricing(input_per_m=15.00, output_per_m=75.00),
    # OpenAI
    "gpt-4o": ModelPricing(input_per_m=2.50, output_per_m=10.00),
    "gpt-4o-mini": ModelPricing(input_per_m=0.15, output_per_m=0.60),
    # Google
    "gemini-1.5-pro": ModelPricing(input_per_m=3.50, output_per_m=10.50),
    "gemini-1.5-flash": ModelPricing(input_per_m=0.075, output_per_m=0.30),
    # Fallback generic tier pricing
    "tier1": ModelPricing(input_per_m=0.25, output_per_m=1.25),
    "tier2": ModelPricing(input_per_m=3.00, output_per_m=15.00),
}


class TranscriptTurnUsage(BaseModel):
    model: str = "claude-3-5-sonnet"
    prompt_tokens: int = Field(default=0, ge=0)
    completion_tokens: int = Field(default=0, ge=0)
    cache_read_tokens: int = Field(default=0, ge=0)
    cache_write_tokens: int = Field(default=0, ge=0)
    cost_usd: float = 0.0


class CostReport(BaseModel):
    session_id: str
    agent_id: str
    total_tokens: int
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cache_tokens: int = 0
    estimated_usd: float  # Transcript-exact USD calculation
    tool_call_counts: Dict[str, int] = Field(default_factory=dict)
    model_breakdown: Dict[str, float] = Field(default_factory=dict)
    mees_score_delta: float = 0.0
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    is_over_budget: bool = False


class CostReportGenerator:
    """Generates transcript-exact cost summaries emitted on session completion and Stop hooks."""

    def __init__(
        self,
        pricing_table: Optional[Dict[str, ModelPricing]] = None,
        budget_usd_ceiling: float = 5.0,
        price_per_1k_tokens: Optional[float] = None,
    ):
        self.pricing = pricing_table or DEFAULT_MODEL_PRICING
        self.budget_ceiling = budget_usd_ceiling
        self.flat_price_per_1k = price_per_1k_tokens

    def calculate_turn_cost(self, turn: TranscriptTurnUsage) -> float:
        """Calculates exact cost for a single transcript turn based on exact tokens and model pricing."""
        norm_model = turn.model.lower().strip()
        matched_pricing = None
        for key, p in self.pricing.items():
            if key in norm_model or norm_model in key:
                matched_pricing = p
                break

        if not matched_pricing:
            matched_pricing = self.pricing.get("tier1", ModelPricing(input_per_m=1.0, output_per_m=3.0))

        input_cost = (turn.prompt_tokens / 1_000_000.0) * matched_pricing.input_per_m
        output_cost = (turn.completion_tokens / 1_000_000.0) * matched_pricing.output_per_m
        cache_read_cost = (turn.cache_read_tokens / 1_000_000.0) * matched_pricing.cache_read_per_m
        cache_write_cost = (turn.cache_write_tokens / 1_000_000.0) * matched_pricing.cache_write_per_m

        return input_cost + output_cost + cache_read_cost + cache_write_cost

    def generate_report_from_turns(
        self,
        session_id: str,
        agent_id: str,
        turns: List[TranscriptTurnUsage],
        tool_calls: List[str],
        mees_delta: float = 0.0,
    ) -> CostReport:
        """Calculates transcript-exact cost summed across all logged turns."""
        total_prompt = 0
        total_completion = 0
        total_cache = 0
        total_cost = 0.0
        model_breakdown: Dict[str, float] = {}

        for turn in turns:
            cost = self.calculate_turn_cost(turn)
            turn.cost_usd = cost
            total_prompt += turn.prompt_tokens
            total_completion += turn.completion_tokens
            total_cache += (turn.cache_read_tokens + turn.cache_write_tokens)
            total_cost += cost
            model_breakdown[turn.model] = model_breakdown.get(turn.model, 0.0) + cost

        total_tokens = total_prompt + total_completion + total_cache
        tool_counts: Dict[str, int] = {}
        for t in tool_calls:
            tool_counts[t] = tool_counts.get(t, 0) + 1

        is_over = total_cost > self.budget_ceiling

        return CostReport(
            session_id=session_id,
            agent_id=agent_id,
            total_tokens=total_tokens,
            prompt_tokens=total_prompt,
            completion_tokens=total_completion,
            cache_tokens=total_cache,
            estimated_usd=round(total_cost, 6),
            tool_call_counts=tool_counts,
            model_breakdown={k: round(v, 6) for k, v in model_breakdown.items()},
            mees_score_delta=mees_delta,
            is_over_budget=is_over,
        )

    def generate_report(
        self,
        session_id: str,
        agent_id: str,
        tokens: int,
        tool_calls: List[str],
        mees_delta: float = 0.0,
        model: str = "claude-3-5-sonnet",
    ) -> CostReport:
        if self.flat_price_per_1k is not None:
            cost = round((tokens / 1000.0) * self.flat_price_per_1k, 4)
            tool_counts: Dict[str, int] = {}
            for t in tool_calls:
                tool_counts[t] = tool_counts.get(t, 0) + 1
            return CostReport(
                session_id=session_id,
                agent_id=agent_id,
                total_tokens=tokens,
                prompt_tokens=int(tokens * 0.75),
                completion_tokens=tokens - int(tokens * 0.75),
                estimated_usd=cost,
                tool_call_counts=tool_counts,
                mees_score_delta=mees_delta,
                is_over_budget=cost > self.budget_ceiling,
            )

        prompt_tokens = int(tokens * 0.75)
        completion_tokens = tokens - prompt_tokens
        turn = TranscriptTurnUsage(
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )
        return self.generate_report_from_turns(
            session_id=session_id,
            agent_id=agent_id,
            turns=[turn],
            tool_calls=tool_calls,
            mees_delta=mees_delta,
        )

    def log_to_audit(self, report: CostReport, logger: LocalAuditLogger) -> AuditEvent:
        event = AuditEvent(
            session_id=report.session_id,
            agent_id=report.agent_id,
            action="CostReport",
            tool=None,
            outcome="alert" if report.is_over_budget else "recorded",
            metadata=report.model_dump(),
        )
        logger.log(event)
        return event

    def format_summary(self, report: CostReport) -> str:
        return (
            f"[CostReport] Session={report.session_id} | Agent={report.agent_id} | "
            f"Tokens={report.total_tokens} (in={report.prompt_tokens}, out={report.completion_tokens}, cache={report.cache_tokens}) | "
            f"Exact Cost=${report.estimated_usd:.4f} | "
            f"Tools={sum(report.tool_call_counts.values())} | MEES Δ={report.mees_score_delta:+0.1f}"
        )
