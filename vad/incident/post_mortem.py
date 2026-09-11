"""Structured Post-Mortem Generator: Converts incident reports and audit slices into formal post-mortems."""

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Dict, List, Optional
import uuid
from pydantic import BaseModel, Field
import yaml

from vad.incident.incident_responder import IncidentReport, IncidentSeverity


class TimelineEntry(BaseModel):
    timestamp: str
    event: str
    actor: str


class ProposedClause(BaseModel):
    kind: str  # "security_invariant" | "functional_invariant" | "constraint" | "proof_obligation"
    description: str


class AlertThreshold(BaseModel):
    metric: str
    threshold: str
    action: str


class PostMortem(BaseModel):
    id: str = Field(default_factory=lambda: f"PM-{uuid.uuid4().hex[:8].upper()}")
    incident_id: str
    title: str
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    severity: str
    timeline: List[TimelineEntry] = Field(default_factory=list)
    contributing_factors: List[str] = Field(default_factory=list)
    invariant_violations: List[str] = Field(default_factory=list)
    root_cause: str
    proposed_eip_clauses: List[ProposedClause] = Field(default_factory=list)
    alert_thresholds: List[AlertThreshold] = Field(default_factory=list)


class PostMortemGenerator:
    """Generates structured, typed post-mortem artifacts from incident reports to close the VAD learning loop."""

    def generate(self, report: IncidentReport) -> PostMortem:
        # 1. Build timeline
        timeline = []
        for ev in report.correlated_events:
            timeline.append(
                TimelineEntry(
                    timestamp=ev.get("timestamp", datetime.now(timezone.utc).isoformat()),
                    event=f"Action '{ev.get('action')}' executed on tool '{ev.get('tool') or 'none'}' with outcome '{ev.get('outcome')}'",
                    actor=ev.get("agent_id", "unknown"),
                )
            )

        if not timeline:
            timeline.append(
                TimelineEntry(
                    timestamp=report.created_at,
                    event=report.summary,
                    actor=", ".join(report.affected_agents),
                )
            )

        # 2. Extract invariant violations and factors
        factors = [
            f"Autonomous agent(s) {', '.join(report.affected_agents)} triggered incident conditions",
            f"Severity level assessed as {report.severity.value}",
        ]
        violations = [
            f"Violation reported during execution under EIP: {report.affected_eip_id or 'unbounded'}"
        ]

        # 3. Propose new EIP clauses
        proposed_clauses = [
            ProposedClause(
                kind="security_invariant",
                description=f"Strictly forbid unverified executions on affected components observed in {report.id}",
            ),
            ProposedClause(
                kind="proof_obligation",
                description=f"Require regression test evidence verifying immunity against {report.summary[:50]}",
            ),
        ]

        # 4. Alert thresholds
        alert_thresholds = [
            AlertThreshold(
                metric="failure_count",
                threshold="> 2 consecutive failures",
                action="quarantine_agent",
            )
        ]

        return PostMortem(
            incident_id=report.id,
            title=f"Post-Mortem: {report.summary}",
            severity=report.severity.value,
            timeline=timeline,
            contributing_factors=factors,
            invariant_violations=violations,
            root_cause=f"Incident caused by {report.summary}. Correlated with {len(report.correlated_events)} audit events.",
            proposed_eip_clauses=proposed_clauses,
            alert_thresholds=alert_thresholds,
        )

    def export_yaml(self, post_mortem: PostMortem, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        data = post_mortem.model_dump()
        path.write_text(yaml.dump(data, sort_keys=False), encoding="utf-8")
        return path
