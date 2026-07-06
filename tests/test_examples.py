import json
import subprocess
import sys

import yaml

from vad.ask.classifier import assess_ask
from vad.contracts.models import EIP
from vad.deploy.models import DeploymentTarget
from vad.evidence.bundle import RunEvidence
from vad.policy.decisions import PolicyDecision
from vad.proof.plan import ProofPlan
from vad.router.models import RouteEvidenceEvent
from vad.server.db.store import ApprovalEvent, DashboardActivity, ServerStore


def test_example_ask_assesses_without_clarification():
    ask = open("examples/ask/sample.txt", encoding="utf-8").read()

    assessment = assess_ask(ask)

    assert assessment.summary
    assert assessment.clarification_questions == []


def test_example_eip_validates():
    data = yaml.safe_load(open("examples/eip/sample.yaml", encoding="utf-8"))

    assert EIP(**data).name == "Sample EIP"


def test_example_proof_plan_validates():
    data = yaml.safe_load(open("examples/proof/sample-proof-plan.yaml", encoding="utf-8"))

    plan = ProofPlan(**data)

    assert plan.mappings[0].obligation_id == "po-1"


def test_example_run_evidence_validates():
    data = json.load(open("examples/evidence/sample-run-evidence.json", encoding="utf-8"))

    evidence = RunEvidence(**data)

    assert evidence.final_decision == "passed"


def test_level3_demo_ask_assesses_as_bounded_release_work():
    ask = open("examples/level3-demo/ask.txt", encoding="utf-8").read()

    assessment = assess_ask(ask)

    assert assessment.release_needed is True
    assert assessment.telemetry_needed is True
    assert "release" in assessment.required_proof_kinds


def test_level3_demo_contract_artifacts_validate():
    eip_data = yaml.safe_load(open("examples/level3-demo/eip.yaml", encoding="utf-8"))
    proof_data = yaml.safe_load(open("examples/level3-demo/proof-plan.yaml", encoding="utf-8"))
    route_data = json.load(open("examples/level3-demo/provider-route.json", encoding="utf-8"))
    target_data = yaml.safe_load(open("examples/level3-demo/deployment-target.yaml", encoding="utf-8"))

    eip = EIP(**eip_data)
    proof_plan = ProofPlan(**proof_data)
    route = RouteEvidenceEvent(**route_data)
    target = DeploymentTarget(**target_data)

    assert eip.name == "Level 3 Demo Status Summary"
    assert proof_plan.covers_obligation("demo-po-unit")
    assert proof_plan.covers_obligation("release_guardian_approval")
    assert route.selected_model == "fake-provider/tier1-local"
    assert target.provider == "fake"


def test_level3_demo_run_evidence_and_dashboard_seed_validate(tmp_path):
    evidence_data = json.load(open("examples/level3-demo/run-evidence.json", encoding="utf-8"))
    dashboard_data = json.load(open("examples/level3-demo/dashboard-seed.json", encoding="utf-8"))

    evidence = RunEvidence(**evidence_data)
    store = ServerStore(tmp_path / "level3-demo.sqlite3")
    stored = store.save_run_evidence(evidence)

    for approval_data in dashboard_data["approvals"]:
        approval_data["evidence_digest"] = stored.evidence_digest
        approval_data["decision"] = PolicyDecision(**approval_data["decision"])
        store.save_approval_event(ApprovalEvent(**approval_data))

    for activity_data in dashboard_data["activity"]:
        activity_data["evidence_digest"] = stored.evidence_digest
        store.save_dashboard_activity(DashboardActivity(**activity_data))

    snapshot = store.dashboard_snapshot()
    clients = snapshot["client_counts"]

    assert snapshot["runs"][0]["run_id"] == "level3-demo-success"
    assert len(store.list_approval_events("level3-demo-success")) == 1
    for client in ["Claude Code", "VSCode", "Codex", "Antigravity", "Cursor", "Windsurf", "OpenCode", "Generic MCP/A2A"]:
        assert clients[client] == 1


def test_level3_demo_fixture_repo_tests_pass():
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "tests"],
        cwd="examples/level3-demo/repo",
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
