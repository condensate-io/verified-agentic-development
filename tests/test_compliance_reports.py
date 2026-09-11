import json
import pytest
from pathlib import Path
from vad.audit.local_audit import LocalAuditLogger, AuditEvent
from vad.compliance.reports import ComplianceReportGenerator, ComplianceFramework


def test_compliance_report_generator_json(tmp_path):
    db_file = tmp_path / "audit.db"
    logger = LocalAuditLogger(sqlite_path=str(db_file), jsonl_path=str(tmp_path / "audit.jsonl"))
    logger.log(AuditEvent(
        session_id="s1",
        agent_id="claude",
        action="edit",
        outcome="success",
    ))

    gen = ComplianceReportGenerator(sqlite_path=str(db_file))
    report = gen.generate_json_report()

    assert report["total_controls_evaluated"] == 4
    assert report["compliant_count"] == 4
    control_ids = [c["control_id"] for c in report["controls"]]
    assert "SOC2-CC6.6" in control_ids
    assert "SOC2-CC7.2" in control_ids
    assert "ISO-42001-B.7" in control_ids
    assert "EU-AI-ACT-ART-14" in control_ids


def test_compliance_report_generator_export(tmp_path):
    gen = ComplianceReportGenerator()
    out = gen.export_report(tmp_path)

    assert out["markdown"].exists()
    assert out["json"].exists()

    md_content = out["markdown"].read_text(encoding="utf-8")
    assert "Verified Agentic Development (VAD) Compliance Attestation Report" in md_content
    assert "✅ COMPLIANT" in md_content
