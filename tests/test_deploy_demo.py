import json
import sys
from pathlib import Path
from unittest.mock import patch

from vad.cli import main
from vad.deploy.demo import run_failed_deployment_demo, run_signed_deployment_demo
from vad.ui.render import render_dashboard, render_run_list


FIXTURE = Path("examples/level3-demo")


def test_signed_deployment_demo_applies_fake_deployment_and_verifies_attestation(tmp_path):
    result = run_signed_deployment_demo(FIXTURE, tmp_path / "deploy")

    assert result.final_decision == "passed"
    assert result.dry_run["side_effects"] is False
    assert result.deployment["status"] == "applied"
    assert result.deployment["provider"] == "fake"
    assert all(gate.passed for gate in result.telemetry)
    assert result.attestation_verified is True
    assert result.attestation["signature"]["payload_digest"]
    assert (tmp_path / "deploy" / "deployment-demo.json").exists()


def test_cli_deploy_demo_writes_signed_deployment_result(tmp_path):
    out_file = tmp_path / "deploy-demo.json"
    artifact_dir = tmp_path / "artifacts"

    with patch.object(sys, "argv", [
        "vad",
        "deploy",
        "demo",
        "--fixture",
        str(FIXTURE),
        "--out-dir",
        str(artifact_dir),
        "--out",
        str(out_file),
    ]):
        main()

    payload = json.loads(out_file.read_text(encoding="utf-8"))
    artifact_payload = json.loads((artifact_dir / "deployment-demo.json").read_text(encoding="utf-8"))

    assert payload["final_decision"] == "passed"
    assert payload["deployment"]["status"] == "applied"
    assert payload["attestation_verified"] is True
    assert artifact_payload == payload


def test_failed_deployment_demo_rolls_back_and_records_feedback_for_ui(tmp_path):
    result = run_failed_deployment_demo(FIXTURE, tmp_path / "failure")

    assert result.final_decision == "blocked"
    assert result.deployment["status"] == "applied"
    assert result.rollback["status"] == "rolled_back"
    assert result.telemetry[0].passed is False
    assert result.feedback_proposals[0]["proposal_type"] == "add_release_gate"
    assert result.run_evidence["final_decision"] == "blocked"
    assert "Rollback triggered" in result.run_evidence["blocker"]

    dashboard_markup = render_dashboard(result.dashboard)
    run_markup = render_run_list(result.dashboard["runs"])
    assert "Rollback triggered" in dashboard_markup
    assert "blocked" in dashboard_markup
    assert "level3-demo-failure" in run_markup


def test_cli_deploy_failure_demo_writes_rollback_and_dashboard_result(tmp_path):
    out_file = tmp_path / "failure-demo.json"
    artifact_dir = tmp_path / "artifacts"

    with patch.object(sys, "argv", [
        "vad",
        "deploy",
        "failure-demo",
        "--fixture",
        str(FIXTURE),
        "--out-dir",
        str(artifact_dir),
        "--out",
        str(out_file),
    ]):
        main()

    payload = json.loads(out_file.read_text(encoding="utf-8"))
    artifact_payload = json.loads((artifact_dir / "failure-demo.json").read_text(encoding="utf-8"))

    assert payload["final_decision"] == "blocked"
    assert payload["rollback"]["status"] == "rolled_back"
    assert payload["dashboard"]["runs"][0]["run_id"] == "level3-demo-failure"
    assert artifact_payload == payload
