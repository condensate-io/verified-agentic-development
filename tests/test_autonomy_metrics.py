"""Tests for Verification & Autonomy Outcome Metrics Engine (vad.audit.metrics)."""

from __future__ import annotations

from contextlib import closing
import json
import sqlite3

import pytest

from vad.audit.metrics import (
    AutonomyMetricsCalculator,
    AutonomyMetricsReport,
    DeliveryOutcomeSample,
    EscapedDefectSample,
)


def test_empty_dataset_defaults() -> None:
    report = AutonomyMetricsCalculator().compute([])

    assert isinstance(report, AutonomyMetricsReport)
    assert report.total_changes == 0
    assert report.accepted_changes == 0
    assert report.autonomous_accepted_changes == 0
    assert report.autonomous_safe_pass_through_rate == 0.0
    assert report.escaped_defect_count == 0
    assert report.escaped_defect_rate == 0.0
    assert report.verification_false_positive_count == 0
    assert report.verification_false_positive_rate == 0.0
    assert report.faulted_runs == 0
    assert report.recovered_runs == 0
    assert report.recovery_success_rate == 1.0
    assert report.human_review_count == 0
    assert report.human_review_time_median_seconds is None
    assert report.human_review_time_p95_seconds is None
    assert report.total_cost_usd == 0.0
    assert report.cost_per_accepted_change_usd is None
    assert report.breakdown_by_risk_tier == {}


def test_mixed_dataset_exact_metrics_across_all_risk_tiers() -> None:
    outcomes = [
        # 1. low: autonomously accepted, clean (safe pass-through)
        DeliveryOutcomeSample(
            run_id="run-low-1",
            evidence_digest="a" * 64,
            risk_tier="low",
            accepted=True,
            autonomous=True,
            cost_usd=0.20,
        ),
        # 2. low: autonomously accepted, BUT has 2 escaped defects -> NOT safe pass-through
        DeliveryOutcomeSample(
            run_id="run-low-2",
            evidence_digest="b" * 64,
            risk_tier="low",
            accepted=True,
            autonomous=True,
            cost_usd=0.30,
        ),
        # 3. medium: autonomously accepted, fault injected & recovered, clean
        DeliveryOutcomeSample(
            run_id="run-med-1",
            evidence_digest="c" * 64,
            risk_tier="medium",
            accepted=True,
            autonomous=True,
            fault_injected=True,
            recovered=True,
            cost_usd=0.50,
        ),
        # 4. medium: rejected (sunk cost!), verification false positive, fault injected & not recovered, human reviewed
        DeliveryOutcomeSample(
            run_id="run-med-2",
            evidence_digest="d" * 64,
            risk_tier="medium",
            accepted=False,
            autonomous=False,
            verification_false_positive=True,
            fault_injected=True,
            recovered=False,
            human_review_seconds=120.0,
            cost_usd=0.80,
        ),
        # 5. high: human-accepted, fault injected & recovered, human reviewed
        DeliveryOutcomeSample(
            run_id="run-high-1",
            evidence_digest="e" * 64,
            risk_tier="high",
            accepted=True,
            autonomous=False,
            fault_injected=True,
            recovered=True,
            human_review_seconds=300.0,
            cost_usd=1.20,
        ),
        # 6. high: human-accepted, has 1 escaped defect, human reviewed
        DeliveryOutcomeSample(
            run_id="run-high-2",
            evidence_digest="f" * 64,
            risk_tier="high",
            accepted=True,
            autonomous=False,
            human_review_seconds=60.0,
            cost_usd=0.90,
        ),
        # 7. critical: human-accepted, clean, human reviewed
        DeliveryOutcomeSample(
            run_id="run-crit-1",
            evidence_digest="1" * 64,
            risk_tier="critical",
            accepted=True,
            autonomous=False,
            human_review_seconds=600.0,
            cost_usd=2.10,
        ),
        # 8. critical: rejected (sunk cost!), verification false positive, human reviewed
        DeliveryOutcomeSample(
            run_id="run-crit-2",
            evidence_digest="2" * 64,
            risk_tier="critical",
            accepted=False,
            autonomous=False,
            verification_false_positive=True,
            human_review_seconds=180.0,
            cost_usd=1.00,
        ),
    ]

    defects = [
        # Two defects on the same autonomously accepted run -> counts as 2 defects, 1 defective run
        EscapedDefectSample(
            defect_id="def-1",
            run_id="run-low-2",
            incident_id="inc-101",
            severity="high",
            summary="Cache invalidation race",
        ),
        EscapedDefectSample(
            defect_id="def-2",
            run_id="run-low-2",
            incident_id="inc-102",
            severity="medium",
            summary="Missing retry jitter",
        ),
        # One defect on human-accepted high-risk run
        EscapedDefectSample(
            defect_id="def-3",
            run_id="run-high-2",
            incident_id="inc-103",
            severity="critical",
            summary="Permission check regression",
        ),
    ]

    report = AutonomyMetricsCalculator.compute(outcomes, defects)

    # Counts
    assert report.total_changes == 8
    assert report.accepted_changes == 6
    assert report.autonomous_accepted_changes == 3

    # 1. Autonomous safe pass-through rate:
    # run-low-1 and run-med-1 are autonomously accepted with 0 defects; run-low-2 has defects -> excluded!
    assert report.autonomous_safe_pass_through_rate == pytest.approx(2 / 8)

    # 2. Escaped defects count & rate:
    # 3 total defect records across 2 distinct accepted runs (run-low-2, run-high-2) out of 6 accepted
    assert report.escaped_defect_count == 3
    assert report.escaped_defect_rate == pytest.approx(2 / 6)

    # 3. Verification false positives count & rate:
    # 2 false positives out of 8 total changes
    assert report.verification_false_positive_count == 2
    assert report.verification_false_positive_rate == pytest.approx(2 / 8)

    # 4. Recovery success rate:
    # 3 faulted runs (med-1, med-2, high-1), 2 recovered (med-1, high-1)
    assert report.faulted_runs == 3
    assert report.recovered_runs == 2
    assert report.recovery_success_rate == pytest.approx(2 / 3)

    # 5. Human review median & p95 latency:
    # Sorted review times: [60.0, 120.0, 180.0, 300.0, 600.0] (5 reviews)
    # Median = 180.0
    # P95 (linear interpolation): pos = 0.95 * (5 - 1) = 3.8 -> 300.0 + 0.8 * (600.0 - 300.0) = 540.0
    assert report.human_review_count == 5
    assert report.human_review_time_median_seconds == pytest.approx(180.0)
    assert report.human_review_time_p95_seconds == pytest.approx(540.0)

    # 6. Cost per accepted change (includes sunk cost of rejected runs med-2 & crit-2):
    # Total = 0.20 + 0.30 + 0.50 + 0.80 + 1.20 + 0.90 + 2.10 + 1.00 = 7.00
    # Accepted = 6 -> 7.00 / 6
    assert report.total_cost_usd == pytest.approx(7.0)
    assert report.cost_per_accepted_change_usd == pytest.approx(7.0 / 6)

    # Breakdown by risk tier
    assert report.breakdown_by_risk_tier == {
        "low": {
            "total": 2,
            "accepted": 2,
            "autonomous_accepted": 2,
            "autonomous_safe": 1,
            "escaped_defects": 2,
            "false_positives": 0,
            "faulted": 0,
            "recovered": 0,
        },
        "medium": {
            "total": 2,
            "accepted": 1,
            "autonomous_accepted": 1,
            "autonomous_safe": 1,
            "escaped_defects": 0,
            "false_positives": 1,
            "faulted": 2,
            "recovered": 1,
        },
        "high": {
            "total": 2,
            "accepted": 2,
            "autonomous_accepted": 0,
            "autonomous_safe": 0,
            "escaped_defects": 1,
            "false_positives": 0,
            "faulted": 1,
            "recovered": 1,
        },
        "critical": {
            "total": 2,
            "accepted": 1,
            "autonomous_accepted": 0,
            "autonomous_safe": 0,
            "escaped_defects": 0,
            "false_positives": 1,
            "faulted": 0,
            "recovered": 0,
        },
    }


def test_rejected_runs_only_yield_none_cost_per_accepted_change() -> None:
    outcomes = [
        DeliveryOutcomeSample(
            run_id="run-rej-1",
            risk_tier="high",
            accepted=False,
            autonomous=False,
            verification_false_positive=True,
            human_review_seconds=45.0,
            cost_usd=1.25,
        ),
    ]

    report = AutonomyMetricsCalculator.compute(outcomes)

    assert report.total_changes == 1
    assert report.accepted_changes == 0
    assert report.escaped_defect_rate == 0.0
    assert report.total_cost_usd == pytest.approx(1.25)
    assert report.cost_per_accepted_change_usd is None
    assert report.human_review_time_median_seconds == pytest.approx(45.0)
    assert report.human_review_time_p95_seconds == pytest.approx(45.0)


def test_compute_from_sqlite_nonexistent_and_missing_tables(tmp_path) -> None:
    missing_db = tmp_path / "does_not_exist.sqlite3"
    report_missing = AutonomyMetricsCalculator.compute_from_sqlite(missing_db)
    assert report_missing.total_changes == 0
    assert report_missing.cost_per_accepted_change_usd is None
    assert report_missing.recovery_success_rate == 1.0
    assert not missing_db.exists()

    # Existing DB from an older schema without delivery_outcomes / defect_links
    legacy_db = tmp_path / "legacy.sqlite3"
    with closing(sqlite3.connect(str(legacy_db))) as conn:
        conn.execute("CREATE TABLE audit_events (id TEXT PRIMARY KEY)")
        conn.commit()

    report_legacy = AutonomyMetricsCalculator.compute_from_sqlite(legacy_db)
    assert report_legacy.total_changes == 0
    assert report_legacy.accepted_changes == 0
    assert report_legacy.human_review_time_median_seconds is None


def test_compute_from_sqlite_seeded_tables(tmp_path) -> None:
    db_path = tmp_path / "vad_v12.sqlite3"
    with closing(sqlite3.connect(str(db_path))) as conn:
        conn.executescript(
            """
            CREATE TABLE delivery_outcomes (
                run_id TEXT PRIMARY KEY,
                evidence_digest TEXT NOT NULL,
                risk_tier TEXT NOT NULL,
                accepted INTEGER NOT NULL,
                autonomous INTEGER NOT NULL,
                verification_false_positive INTEGER NOT NULL DEFAULT 0,
                fault_injected INTEGER NOT NULL DEFAULT 0,
                recovered INTEGER NOT NULL DEFAULT 0,
                human_review_seconds REAL,
                cost_usd REAL NOT NULL DEFAULT 0.0
            );
            CREATE TABLE defect_links (
                defect_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                incident_id TEXT NOT NULL,
                severity TEXT NOT NULL,
                summary TEXT NOT NULL
            );
            """
        )
        conn.execute(
            """
            INSERT INTO delivery_outcomes (
                run_id, evidence_digest, risk_tier, accepted, autonomous,
                verification_false_positive, fault_injected, recovered,
                human_review_seconds, cost_usd
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("run-1", "a" * 64, "low", 1, 1, 0, 0, 0, None, 0.25),
        )
        conn.execute(
            """
            INSERT INTO delivery_outcomes (
                run_id, evidence_digest, risk_tier, accepted, autonomous,
                verification_false_positive, fault_injected, recovered,
                human_review_seconds, cost_usd
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("run-2", "b" * 64, "medium", 1, 1, 0, 1, 1, None, 0.75),
        )
        conn.execute(
            """
            INSERT INTO delivery_outcomes (
                run_id, evidence_digest, risk_tier, accepted, autonomous,
                verification_false_positive, fault_injected, recovered,
                human_review_seconds, cost_usd
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("run-3", "c" * 64, "high", 0, 0, 1, 1, 0, 90.0, 0.50),
        )
        conn.execute(
            """
            INSERT INTO defect_links (defect_id, run_id, incident_id, severity, summary)
            VALUES (?, ?, ?, ?, ?)
            """,
            ("def-1", "run-2", "inc-200", "high", "Post-merge edge case"),
        )
        conn.commit()

    report = AutonomyMetricsCalculator.compute_from_sqlite(db_path)

    assert report.total_changes == 3
    assert report.accepted_changes == 2
    assert report.autonomous_accepted_changes == 2
    # run-1 is safe; run-2 had an escaped defect so it is excluded from safe pass-through
    assert report.autonomous_safe_pass_through_rate == pytest.approx(1 / 3)
    assert report.escaped_defect_count == 1
    assert report.escaped_defect_rate == pytest.approx(1 / 2)
    assert report.verification_false_positive_count == 1
    assert report.verification_false_positive_rate == pytest.approx(1 / 3)
    assert report.faulted_runs == 2
    assert report.recovered_runs == 1
    assert report.recovery_success_rate == pytest.approx(0.5)
    assert report.human_review_count == 1
    assert report.human_review_time_median_seconds == pytest.approx(90.0)
    assert report.human_review_time_p95_seconds == pytest.approx(90.0)
    assert report.total_cost_usd == pytest.approx(1.50)
    assert report.cost_per_accepted_change_usd == pytest.approx(0.75)


def test_compute_from_sqlite_supports_payload_json_and_missing_defect_table(tmp_path) -> None:
    db_path = tmp_path / "vad_payload_only.sqlite3"
    payload = {
        "run_id": "run-json-1",
        "evidence_digest": "d" * 64,
        "risk_tier": "critical",
        "accepted": True,
        "autonomous": False,
        "verification_false_positive": False,
        "fault_injected": False,
        "recovered": False,
        "human_review_seconds": 240.0,
        "cost_usd": 1.60,
    }
    with closing(sqlite3.connect(str(db_path))) as conn:
        conn.execute("CREATE TABLE delivery_outcomes (run_id TEXT PRIMARY KEY, payload_json TEXT NOT NULL)")
        conn.execute(
            "INSERT INTO delivery_outcomes (run_id, payload_json) VALUES (?, ?)",
            ("run-json-1", json.dumps(payload)),
        )
        conn.commit()

    report = AutonomyMetricsCalculator.compute_from_sqlite(db_path)
    assert report.total_changes == 1
    assert report.accepted_changes == 1
    assert report.autonomous_accepted_changes == 0
    assert report.autonomous_safe_pass_through_rate == 0.0
    assert report.escaped_defect_count == 0
    assert report.human_review_time_median_seconds == pytest.approx(240.0)
    assert report.cost_per_accepted_change_usd == pytest.approx(1.60)
