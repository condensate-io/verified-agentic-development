"""Analytics API: FastAPI microservice exposing governance metrics, cost tracking, and ROI telemetry."""

from pathlib import Path
import sqlite3
from typing import Any, Dict, List, Optional
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel

from vad.ui.tui.analytics_panel import AnalyticsCalculator


def create_app(sqlite_path: str = "audit/audit.db") -> FastAPI:
    """Factory creating the FastAPI metrics dashboard application."""
    app = FastAPI(
        title="VAD Governance Analytics API",
        description="REST endpoints providing real-time telemetry, cost reports, and ROI analytics for VAD.",
        version="1.0.0",
    )
    calculator = AnalyticsCalculator(sqlite_path=sqlite_path)

    @app.get("/metrics/cost")
    def get_cost_metrics() -> Dict[str, Any]:
        metrics = calculator.compute_metrics()
        return {
            "total_tokens": metrics["total_tokens"],
            "estimated_cost_usd": metrics["estimated_cost_usd"],
            "currency": "USD",
        }

    @app.get("/metrics/roi")
    def get_roi_metrics() -> Dict[str, Any]:
        metrics = calculator.compute_metrics()
        hours = metrics["roi_hours_saved"]
        # Assuming enterprise blended engineering rate of $120/hr
        dollar_value = round(hours * 120.0, 2)
        net_savings = round(dollar_value - metrics["estimated_cost_usd"], 2)
        return {
            "engineering_hours_saved": hours,
            "hourly_rate_usd": 120.0,
            "gross_savings_usd": dollar_value,
            "net_savings_usd": net_savings,
            "success_rate_pct": metrics["success_rate"],
        }

    @app.get("/metrics/agent-utilization")
    def get_agent_utilization() -> Dict[str, Any]:
        metrics = calculator.compute_metrics()
        return {
            "total_events": metrics["total_events"],
            "utilization": metrics["agent_utilization"],
        }

    @app.get("/metrics/trends")
    def get_trends() -> Dict[str, Any]:
        p = Path(sqlite_path)
        if not p.exists():
            return {"timeline": []}

        timeline = []
        try:
            with sqlite3.connect(sqlite_path) as conn:
                cur = conn.cursor()
                cur.execute(
                    "SELECT substr(timestamp, 1, 13) as hour_bucket, COUNT(*), outcome FROM audit_events GROUP BY hour_bucket, outcome ORDER BY hour_bucket"
                )
                for bucket, count, outcome in cur.fetchall():
                    timeline.append({"bucket": bucket, "count": count, "outcome": outcome})
        except Exception:
            pass

        return {"timeline": timeline}

    @app.get("/metrics/autonomy")
    def get_autonomy_metrics() -> Dict[str, Any]:
        from vad.audit.metrics import AutonomyMetricsCalculator

        return AutonomyMetricsCalculator().compute_from_sqlite(sqlite_path).model_dump(mode="json")

    return app
