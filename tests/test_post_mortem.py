import pytest
import yaml
from pathlib import Path
from vad.incident.incident_responder import IncidentReport, IncidentSeverity
from vad.incident.post_mortem import PostMortemGenerator, PostMortem


def test_post_mortem_generator_basic(tmp_path):
    report = IncidentReport(
        severity=IncidentSeverity.SEV2,
        summary="Integrity checker blocked unauthorized modification of skill descriptor",
        affected_eip_id="eip-checkout-1",
        affected_agents=["claude-worker"],
        correlated_events=[
            {"timestamp": "2026-09-11T12:00:00Z", "action": "pre_tool_block", "agent_id": "claude-worker", "outcome": "failure"}
        ],
    )

    gen = PostMortemGenerator()
    pm = gen.generate(report)

    assert isinstance(pm, PostMortem)
    assert pm.severity == "SEV-2"
    assert pm.incident_id == report.id
    assert len(pm.timeline) == 1
    assert len(pm.proposed_eip_clauses) >= 2

    # Verify YAML export
    out_file = tmp_path / "post_mortem.yaml"
    gen.export_yaml(pm, out_file)
    assert out_file.exists()

    loaded = yaml.safe_load(out_file.read_text(encoding="utf-8"))
    assert loaded["severity"] == "SEV-2"
    assert "proposed_eip_clauses" in loaded
