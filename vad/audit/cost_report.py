"""Cost Report Generator: Aggregates per-session token consumption, USD estimates, and tool counts."""

from datetime import datetime, timezone
from typing import Dict, List, Optional
from pydantic import BaseModel, Field
from vad.audit.local_audit import AuditEvent, LocalAuditLogger


class CostReport(BaseModel):
    session_id: str
    agent_id: str
    total_tokens: int
    estimated_usd: float
    tool_call_counts: Dict[str, int] = Field(default_factory=dict)
    mees_score_delta: float = 0.0
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    is_over_budget: bool = False


class CostReportGenerator:
    """Generates cost and token consumption summaries emitted on session completion and Stop hooks."""

    def __init__(self, price_per_1k_tokens: float = 0.003, budget_usd_ceiling: float = 5.0):
        self.price_per_1k = price_per_1k_tokens
        self.budget_ceiling = budget_usd_ceiling

    def generate_report(
        self,
        session_id: str,
        agent_id: str,
        tokens: int,
        tool_calls: List[str],
        mees_delta: float = 0.0,
    ) -> CostReport:
        cost = round((tokens / 1000.0) * self.price_per_1k, 4)
        tool_counts: Dict[str, int] = {}
        for t in tool_calls:
            tool_counts[t] = tool_counts.get(t, 0) + 1

        is_over = cost > self.budget_ceiling

        return CostReport(
            session_id=session_id,
            agent_id=agent_id,
            total_tokens=tokens,
            estimated_usd=cost,
            tool_call_counts=tool_counts,
            mees_score_delta=mees_delta,
            is_over_budget=is_over,
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
            f"Tokens={report.total_tokens} | Cost=${report.estimated_usd:.4f} | "
            f"Tools={sum(report.tool_call_counts.values())} | MEES Δ={report.mees_score_delta:+0.1f}"
        )
