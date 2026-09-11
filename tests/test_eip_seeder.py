import pytest
import yaml
from pathlib import Path
from vad.incident.post_mortem import PostMortem, ProposedClause, TimelineEntry, AlertThreshold
from vad.incident.eip_seeder import EIPSeeder, main
from vad.contracts.models import EIP, RiskTier


def test_eip_seeder_from_post_mortem(tmp_path):
    pm = PostMortem(
        id="PM-SEC-01",
        incident_id="INC-A1B2C3D4",
        title="Unauthorized tool execution incident",
        timestamp="2026-09-11T12:00:00Z",
        severity="SEV-1",
        timeline=[TimelineEntry(timestamp="2026-09-11T12:00:00Z", event="Action blocked", actor="claude")],
        contributing_factors=["Missing integrity check"],
        invariant_violations=["Integrity drift"],
        root_cause="Hash mismatch in skill descriptor",
        proposed_eip_clauses=[
            ProposedClause(kind="security_invariant", description="All invoked skills must match integrity registry SHA256"),
            ProposedClause(kind="proof_obligation", description="Verify security invariant under adversarial test"),
        ],
        alert_thresholds=[AlertThreshold(metric="integrity_violations", threshold="> 0", action="halt")],
    )

    seeder = EIPSeeder()
    eip = seeder.seed_from_post_mortem(pm, output_dir=tmp_path)

    assert isinstance(eip, EIP)
    assert eip.risk_tier == RiskTier.HIGH
    assert "All invoked skills must match" in eip.invariants.security[0]
    assert len(eip.proof_obligations) == 1

    out_file = tmp_path / f"{eip.name}.yaml"
    assert out_file.exists()


def test_eip_seeder_cli(tmp_path):
    pm_file = tmp_path / "pm.yaml"
    pm_data = {
        "id": "PM-TEST",
        "incident_id": "INC-TEST",
        "title": "Test Incident",
        "timestamp": "2026-09-11T12:00:00Z",
        "severity": "SEV-2",
        "timeline": [{"timestamp": "2026-09-11T12:00:00Z", "event": "err", "actor": "worker"}],
        "contributing_factors": ["factor"],
        "invariant_violations": ["violation"],
        "root_cause": "root",
        "proposed_eip_clauses": [{"kind": "security_invariant", "description": "Strict check"}],
        "alert_thresholds": [{"metric": "m", "threshold": "t", "action": "a"}],
    }
    pm_file.write_text(yaml.dump(pm_data), encoding="utf-8")

    out_dir = tmp_path / "seeded"
    ret = main(["--post-mortem", str(pm_file), "--output-dir", str(out_dir)])
    assert ret == 0
    assert (out_dir / "remediation-inc-test.yaml").exists()
