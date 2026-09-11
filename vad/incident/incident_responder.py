"""Incident Responder: Triage coordinator extracting correlated audit trails to produce structured incident reports."""

from datetime import datetime, timezone
from enum import Enum
import json
from pathlib import Path
import sqlite3
from typing import Any, Dict, List, Optional
import uuid
from pydantic import BaseModel, Field


class IncidentSeverity(str, Enum):
    SEV1 = "SEV-1"  # Critical breach / production block
    SEV2 = "SEV-2"  # Major invariant violation
    SEV3 = "SEV-3"  # Non-blocking tool or test failure
    SEV4 = "SEV-4"  # Telemetry or formatting anomaly


class IncidentTrigger(BaseModel):
    trigger_type: str
    description: str
    session_id: Optional[str] = None
    eip_id: Optional[str] = None
    severity_hint: Optional[IncidentSeverity] = None


class IncidentReport(BaseModel):
    id: str = Field(default_factory=lambda: f"INC-{uuid.uuid4().hex[:8].upper()}")
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    severity: IncidentSeverity
    summary: str
    affected_eip_id: Optional[str] = None
    affected_agents: List[str] = Field(default_factory=list)
    correlated_events: List[Dict[str, Any]] = Field(default_factory=list)
    suggested_actions: List[str] = Field(default_factory=list)


class IncidentResponder:
    """Coordinates incident investigation by slicing audit logs and correlating with EIP boundaries."""

    def __init__(self, sqlite_path: str = "audit/audit.db"):
        self.sqlite_path = sqlite_path

    def triage(self, trigger: IncidentTrigger) -> IncidentReport:
        correlated = []
        affected_agents = set()

        p = Path(self.sqlite_path)
        if p.exists():
            try:
                with sqlite3.connect(self.sqlite_path) as conn:
                    cur = conn.cursor()
                    if trigger.session_id:
                        cur.execute(
                            "SELECT id, timestamp, agent_id, action, tool, outcome, metadata_json FROM audit_events WHERE session_id = ? ORDER BY timestamp DESC LIMIT 50",
                            (trigger.session_id,),
                        )
                    elif trigger.eip_id:
                        cur.execute(
                            "SELECT id, timestamp, agent_id, action, tool, outcome, metadata_json FROM audit_events WHERE eip_id = ? ORDER BY timestamp DESC LIMIT 50",
                            (trigger.eip_id,),
                        )
                    else:
                        cur.execute(
                            "SELECT id, timestamp, agent_id, action, tool, outcome, metadata_json FROM audit_events ORDER BY timestamp DESC LIMIT 20"
                        )

                    for row in cur.fetchall():
                        ev = {
                            "id": row[0],
                            "timestamp": row[1],
                            "agent_id": row[2],
                            "action": row[3],
                            "tool": row[4],
                            "outcome": row[5],
                        }
                        affected_agents.add(row[2])
                        correlated.append(ev)
            except Exception:
                pass

        # Severity determination
        if trigger.severity_hint:
            severity = trigger.severity_hint
        elif any(e.get("action") == "integrity_violation" for e in correlated):
            severity = IncidentSeverity.SEV1
        elif any(e.get("outcome") == "failure" for e in correlated):
            severity = IncidentSeverity.SEV2
        else:
            severity = IncidentSeverity.SEV3

        suggested = [
            "Quarantine offending agent runner and expire lock in DAG",
            "Generate formal post-mortem to synthesize new EIP invariants",
        ]

        return IncidentReport(
            severity=severity,
            summary=f"Incident triage triggered by {trigger.trigger_type}: {trigger.description}",
            affected_eip_id=trigger.eip_id,
            affected_agents=list(affected_agents) or ["unknown-agent"],
            correlated_events=correlated,
            suggested_actions=suggested,
        )
