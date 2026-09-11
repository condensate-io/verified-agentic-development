import pytest
from pathlib import Path
from vad.audit.local_audit import LocalAuditLogger, AuditEvent
from vad.incident.incident_responder import (
    IncidentResponder,
    IncidentTrigger,
    IncidentSeverity,
    IncidentReport,
)


def test_incident_responder_triage(tmp_path):
    db_file = tmp_path / "audit.db"
    logger = LocalAuditLogger(sqlite_path=str(db_file), jsonl_path=str(tmp_path / "audit.jsonl"))

    logger.log(AuditEvent(
        session_id="sess-err",
        agent_id="claude-rogue",
        action="integrity_violation",
        outcome="failure",
        eip_id="eip-auth",
    ))

    responder = IncidentResponder(sqlite_path=str(db_file))
    trigger = IncidentTrigger(
        trigger_type="pre_tool_use_block",
        description="Integrity hash mismatch on skill invocation",
        session_id="sess-err",
    )

    report = responder.triage(trigger)
    assert isinstance(report, IncidentReport)
    assert report.severity == IncidentSeverity.SEV1
    assert "claude-rogue" in report.affected_agents
    assert len(report.correlated_events) == 1
    assert len(report.suggested_actions) > 0
