"""Verification & Autonomy Outcome Metrics Engine.

Computes operational autonomy, quality, reliability, human-review latency, and
cost-efficiency metrics from delivery outcomes and escaped defect links, either
in memory or directly from a VAD SQLite store.
"""

from __future__ import annotations

from contextlib import closing
import json
import math
from pathlib import Path
import sqlite3
import statistics
from typing import Any

from pydantic import BaseModel, Field


class DeliveryOutcomeSample(BaseModel):
    """Single delivery run outcome record used for autonomy metric calculation."""

    run_id: str
    evidence_digest: str = ""
    risk_tier: str = "low"
    accepted: bool
    autonomous: bool
    verification_false_positive: bool = False
    fault_injected: bool = False
    recovered: bool = False
    human_review_seconds: float | None = None
    cost_usd: float = Field(default=0.0, ge=0.0)


class EscapedDefectSample(BaseModel):
    """Post-release incident or revert linked to an accepted delivery run."""

    defect_id: str
    run_id: str
    incident_id: str
    severity: str = "high"
    summary: str = ""


class AutonomyMetricsReport(BaseModel):
    """Aggregated verification and autonomy operational metrics report."""

    total_changes: int
    accepted_changes: int
    autonomous_accepted_changes: int
    autonomous_safe_pass_through_rate: float
    escaped_defect_count: int
    escaped_defect_rate: float
    verification_false_positive_count: int
    verification_false_positive_rate: float
    faulted_runs: int
    recovered_runs: int
    recovery_success_rate: float
    human_review_count: int
    human_review_time_median_seconds: float | None
    human_review_time_p95_seconds: float | None
    total_cost_usd: float
    cost_per_accepted_change_usd: float | None
    breakdown_by_risk_tier: dict[str, dict[str, int]]


def _percentile(sorted_values: list[float], pct: float) -> float:
    """Compute a linear-interpolated percentile (0-100) on a non-empty sorted list."""
    if len(sorted_values) == 1:
        return float(sorted_values[0])
    pos = (pct / 100.0) * (len(sorted_values) - 1)
    lo = math.floor(pos)
    hi = math.ceil(pos)
    if lo == hi:
        return float(sorted_values[lo])
    frac = pos - lo
    return float(sorted_values[lo] + frac * (sorted_values[hi] - sorted_values[lo]))


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    """Convert a SQLite row to a dictionary, merging optional payload_json fields."""
    raw = dict(row)
    merged: dict[str, Any] = {}
    payload_raw = raw.get("payload_json")
    if isinstance(payload_raw, str) and payload_raw.strip():
        parsed = json.loads(payload_raw)
        if isinstance(parsed, dict):
            merged.update(parsed)
    for key, value in raw.items():
        if key != "payload_json" and (value is not None or key not in merged):
            merged[key] = value
    return merged


def _outcome_from_row(row: sqlite3.Row) -> DeliveryOutcomeSample:
    """Hydrate a DeliveryOutcomeSample from a SQLite delivery_outcomes row."""
    data = _row_to_dict(row)
    review_raw = data.get("human_review_seconds")
    cost_raw = data.get("cost_usd")
    return DeliveryOutcomeSample(
        run_id=str(data.get("run_id") or ""),
        evidence_digest=str(data.get("evidence_digest") or ""),
        risk_tier=str(data.get("risk_tier") or "low"),
        accepted=bool(data.get("accepted", False)),
        autonomous=bool(data.get("autonomous", False)),
        verification_false_positive=bool(data.get("verification_false_positive", False)),
        fault_injected=bool(data.get("fault_injected", False)),
        recovered=bool(data.get("recovered", False)),
        human_review_seconds=float(review_raw) if review_raw is not None else None,
        cost_usd=float(cost_raw) if cost_raw is not None else 0.0,
    )


def _defect_from_row(row: sqlite3.Row) -> EscapedDefectSample:
    """Hydrate an EscapedDefectSample from a SQLite defect_links row."""
    data = _row_to_dict(row)
    return EscapedDefectSample(
        defect_id=str(data.get("defect_id") or data.get("id") or ""),
        run_id=str(data.get("run_id") or ""),
        incident_id=str(data.get("incident_id") or ""),
        severity=str(data.get("severity") or "high"),
        summary=str(data.get("summary") or ""),
    )


class AutonomyMetricsCalculator:
    """Computes verification & autonomy operational metrics from outcomes and defect links."""

    @staticmethod
    def compute(
        outcomes: list[DeliveryOutcomeSample],
        defects: list[EscapedDefectSample] | None = None,
    ) -> AutonomyMetricsReport:
        """Compute all autonomy, defect, recovery, review latency, and cost metrics."""
        defect_list = defects or []
        defect_count_by_run: dict[str, int] = {}
        for defect in defect_list:
            defect_count_by_run[defect.run_id] = defect_count_by_run.get(defect.run_id, 0) + 1

        total_changes = len(outcomes)
        accepted_changes = 0
        autonomous_accepted_changes = 0
        autonomous_safe_count = 0
        verification_false_positive_count = 0
        faulted_runs = 0
        recovered_runs = 0
        review_times: list[float] = []
        accepted_runs_with_defects: set[str] = set()
        seen_defect_runs: set[str] = set()
        breakdown: dict[str, dict[str, int]] = {}

        for outcome in outcomes:
            tier = (outcome.risk_tier or "unknown").lower()
            tier_stats = breakdown.setdefault(
                tier,
                {
                    "total": 0,
                    "accepted": 0,
                    "autonomous_accepted": 0,
                    "autonomous_safe": 0,
                    "escaped_defects": 0,
                    "false_positives": 0,
                    "faulted": 0,
                    "recovered": 0,
                },
            )
            tier_stats["total"] += 1

            run_defects = defect_count_by_run.get(outcome.run_id, 0)
            if outcome.run_id not in seen_defect_runs:
                tier_stats["escaped_defects"] += run_defects
                seen_defect_runs.add(outcome.run_id)

            if outcome.accepted:
                accepted_changes += 1
                tier_stats["accepted"] += 1
                if run_defects > 0:
                    accepted_runs_with_defects.add(outcome.run_id)
                if outcome.autonomous:
                    autonomous_accepted_changes += 1
                    tier_stats["autonomous_accepted"] += 1
                    if run_defects == 0:
                        autonomous_safe_count += 1
                        tier_stats["autonomous_safe"] += 1

            if outcome.verification_false_positive:
                verification_false_positive_count += 1
                tier_stats["false_positives"] += 1

            if outcome.fault_injected:
                faulted_runs += 1
                tier_stats["faulted"] += 1
                if outcome.recovered:
                    recovered_runs += 1
                    tier_stats["recovered"] += 1

            if outcome.human_review_seconds is not None:
                review_times.append(float(outcome.human_review_seconds))

        review_times.sort()
        human_review_count = len(review_times)
        total_cost_usd = math.fsum(outcome.cost_usd for outcome in outcomes)

        return AutonomyMetricsReport(
            total_changes=total_changes,
            accepted_changes=accepted_changes,
            autonomous_accepted_changes=autonomous_accepted_changes,
            autonomous_safe_pass_through_rate=(
                autonomous_safe_count / total_changes if total_changes > 0 else 0.0
            ),
            escaped_defect_count=len(defect_list),
            escaped_defect_rate=(
                len(accepted_runs_with_defects) / accepted_changes if accepted_changes > 0 else 0.0
            ),
            verification_false_positive_count=verification_false_positive_count,
            verification_false_positive_rate=(
                verification_false_positive_count / total_changes if total_changes > 0 else 0.0
            ),
            faulted_runs=faulted_runs,
            recovered_runs=recovered_runs,
            recovery_success_rate=(recovered_runs / faulted_runs if faulted_runs > 0 else 1.0),
            human_review_count=human_review_count,
            human_review_time_median_seconds=(
                float(statistics.median(review_times)) if human_review_count > 0 else None
            ),
            human_review_time_p95_seconds=(
                _percentile(review_times, 95.0) if human_review_count > 0 else None
            ),
            total_cost_usd=total_cost_usd,
            cost_per_accepted_change_usd=(
                total_cost_usd / accepted_changes if accepted_changes > 0 else None
            ),
            breakdown_by_risk_tier=breakdown,
        )

    @classmethod
    def compute_from_sqlite(cls, sqlite_path: str | Path) -> AutonomyMetricsReport:
        """Load delivery_outcomes & defect_links from SQLite, returning zeroed metrics if absent."""
        db_path = Path(sqlite_path)
        if not db_path.is_file():
            return cls.compute([])

        with closing(sqlite3.connect(str(db_path))) as conn:
            conn.row_factory = sqlite3.Row
            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type='table' AND name IN ('delivery_outcomes', 'defect_links')"
                ).fetchall()
            }
            if "delivery_outcomes" not in tables:
                return cls.compute([])

            outcome_rows = conn.execute("SELECT * FROM delivery_outcomes").fetchall()
            outcomes = [_outcome_from_row(row) for row in outcome_rows]

            defects: list[EscapedDefectSample] = []
            if "defect_links" in tables:
                defect_rows = conn.execute("SELECT * FROM defect_links").fetchall()
                defects = [_defect_from_row(row) for row in defect_rows]

        return cls.compute(outcomes, defects)
