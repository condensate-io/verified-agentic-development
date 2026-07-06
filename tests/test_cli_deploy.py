import json
import sys
from unittest.mock import patch

import yaml
import pytest

from vad.cli import main


EIP = """
version: 1.0.0
name: deploy-sample
risk_tier: medium
autonomy_tier: bounded
goal:
  description: Deploy sample
  success_criteria: ["Deployment plan exists"]
non_goals: []
scope_boundaries: ["deploy"]
invariants: {}
constraints: {}
proof_obligations: []
tool_permissions:
  allowed: ["pytest"]
  denied: ["network"]
memory_requirements: []
model_budget:
  max_tokens: 1000
  max_cost: 1.0
  max_loop_depth: 3
release_requirements:
  required: true
  gates: ["approval"]
telemetry_requirements:
  required: true
  signals: ["healthy_requests"]
"""


TARGET = {
    "target_id": "prod-api",
    "environment": "production",
    "provider": "fake",
    "region": "local",
    "strategy": "blue-green",
    "artifact_digest": "a" * 64,
    "telemetry": [
        {
            "name": "healthy_requests",
            "query": "rate(http_requests_total[5m])",
            "minimum_health": 0.99,
            "window_seconds": 300,
        }
    ],
    "rollback": {
        "enabled": True,
        "strategy": "blue-green",
        "trigger_metric": "healthy_requests",
        "threshold": 0.95,
        "max_wait_seconds": 600,
    },
}


def write_inputs(tmp_path):
    eip_file = tmp_path / "eip.yaml"
    target_file = tmp_path / "target.yaml"
    eip_file.write_text(EIP)
    target_file.write_text(yaml.safe_dump(TARGET))
    return eip_file, target_file


def test_cli_deploy_plan_writes_plan(tmp_path):
    eip_file, target_file = write_inputs(tmp_path)
    plan_file = tmp_path / "plan.yaml"

    with patch.object(sys, "argv", ["vad", "deploy", "plan", str(eip_file), str(target_file), "--out", str(plan_file)]):
        main()

    plan = yaml.safe_load(plan_file.read_text())
    assert plan["plan_id"] == "deploy-sample-prod-api"
    assert plan["target"]["target_id"] == "prod-api"


def test_cli_deploy_dry_run_writes_no_side_effect_evidence(tmp_path):
    eip_file, target_file = write_inputs(tmp_path)
    plan_file = tmp_path / "plan.yaml"
    out_file = tmp_path / "dry-run.json"

    with patch.object(sys, "argv", ["vad", "deploy", "plan", str(eip_file), str(target_file), "--out", str(plan_file)]):
        main()
    with patch.object(sys, "argv", ["vad", "deploy", "dry-run", str(plan_file), "--out", str(out_file)]):
        main()

    payload = json.loads(out_file.read_text())
    assert payload["event"] == "deployment_dry_run"
    assert payload["side_effects"] is False


def test_cli_deploy_apply_requires_approval_for_production(tmp_path):
    eip_file, target_file = write_inputs(tmp_path)
    plan_file = tmp_path / "plan.yaml"
    out_file = tmp_path / "apply.json"

    with patch.object(sys, "argv", ["vad", "deploy", "plan", str(eip_file), str(target_file), "--out", str(plan_file)]):
        main()
    with patch.object(sys, "argv", ["vad", "deploy", "apply", str(plan_file), "--out", str(out_file)]):
        with pytest.raises(SystemExit) as exc:
            main()

    assert exc.value.code == 1
    payload = json.loads(out_file.read_text())
    assert payload["decision"]["allow"] is False
    assert "production deployment requires approval" in payload["decision"]["denials"]


def test_cli_deploy_apply_and_rollback_record_evidence(tmp_path):
    eip_file, target_file = write_inputs(tmp_path)
    plan_file = tmp_path / "plan.yaml"
    apply_file = tmp_path / "apply.json"
    rollback_file = tmp_path / "rollback.json"

    with patch.object(sys, "argv", ["vad", "deploy", "plan", str(eip_file), str(target_file), "--out", str(plan_file)]):
        main()
    with patch.object(
        sys,
        "argv",
        ["vad", "deploy", "apply", str(plan_file), "--approval-ref", "approval:prod-1", "--out", str(apply_file)],
    ):
        main()
    with patch.object(
        sys,
        "argv",
        [
            "vad",
            "deploy",
            "rollback",
            str(apply_file),
            "--rollback-approval-ref",
            "approval:rollback-1",
            "--out",
            str(rollback_file),
        ],
    ):
        main()

    applied = json.loads(apply_file.read_text())
    rollback = json.loads(rollback_file.read_text())
    assert applied["deployment"]["status"] == "applied"
    assert rollback["rollback"]["status"] == "rolled_back"
