import sys
import pytest
import yaml
import json
from unittest.mock import patch
from vad.cli import main
from vad.evidence.bundle import AgentEvidence, EffortEvidence, EvidenceBundle, EvidenceRef, RunEvidence, TokenEvidence, VerificationEvidence
from vad.effort.scoring import DiffMetrics
from pathlib import Path

VALID_EIP_YAML = '''
version: 1.0.0
name: Sample
risk_tier: low
autonomy_tier: assisted
goal:
  description: Test
  success_criteria: ["Pass"]
non_goals: []
scope_boundaries: ["tests"]
invariants: {}
constraints: {}
proof_obligations: []
tool_permissions:
  allowed: ["pytest"]
  denied: ["network"]
memory_requirements:
  - scope: project
    purpose: cli validation
model_budget:
  max_tokens: 1000
  max_cost: 1.0
  max_loop_depth: 3
release_requirements:
  required: false
  gates: []
telemetry_requirements:
  required: false
  signals: []
'''

def test_cli_validate_valid_yaml(tmp_path):
    sample_yaml = tmp_path / "sample.yaml"
    sample_yaml.write_text(VALID_EIP_YAML)
    
    test_args = ["vad", "eip", "validate", str(sample_yaml)]
    with patch.object(sys, 'argv', test_args):
        try:
            main()
        except SystemExit as e:
            assert e.code == 0

def test_cli_validate_invalid_yaml(tmp_path):
    sample_yaml = tmp_path / "invalid.yaml"
    sample_yaml.write_text('''
version: 1.0.0
name: Sample
risk_tier: invalid_tier
autonomy_tier: assisted
goal:
  description: Test
  success_criteria: ["Pass"]
non_goals: []
scope_boundaries: ["tests"]
invariants: {}
constraints: {}
proof_obligations: []
tool_permissions:
  allowed: ["pytest"]
  denied: ["network"]
memory_requirements:
  - scope: project
    purpose: cli validation
model_budget:
  max_tokens: 1000
  max_cost: 1.0
  max_loop_depth: 3
release_requirements:
  required: false
  gates: []
telemetry_requirements:
  required: false
  signals: []
''')
    
    test_args = ["vad", "eip", "validate", str(sample_yaml)]
    with patch.object(sys, 'argv', test_args):
        with pytest.raises(SystemExit) as e:
            main()
        assert e.value.code == 1

def test_cli_normalize_writes_valid_stable_yaml(tmp_path):
    source = tmp_path / "source.yaml"
    first = tmp_path / "first.yaml"
    second = tmp_path / "second.yaml"
    source.write_text(VALID_EIP_YAML.replace('allowed: ["pytest"]', 'allowed: ["pytest", "read_file"]'))

    with patch.object(sys, 'argv', ["vad", "eip", "normalize", str(source), "--out", str(first)]):
        main()

    with patch.object(sys, 'argv', ["vad", "eip", "normalize", str(first), "--out", str(second)]):
        main()

    assert first.read_text() == second.read_text()
    normalized = yaml.safe_load(first.read_text())
    assert normalized["tool_permissions"]["allowed"] == ["pytest", "read_file"]

    with patch.object(sys, 'argv', ["vad", "eip", "validate", str(first)]):
        main()

def test_cli_normalize_refuses_overwrite_without_force(tmp_path):
    source = tmp_path / "source.yaml"
    output = tmp_path / "output.yaml"
    source.write_text(VALID_EIP_YAML)
    output.write_text("existing")

    with patch.object(sys, 'argv', ["vad", "eip", "normalize", str(source), "--out", str(output)]):
        with pytest.raises(SystemExit) as e:
            main()

    assert e.value.code == 1
    assert output.read_text() == "existing"

def test_cli_init_creates_valid_eip_template(tmp_path):
    output = tmp_path / "new-eip.yaml"

    with patch.object(sys, 'argv', ["vad", "eip", "init", "--name", "new-feature", "--out", str(output)]):
        main()

    data = yaml.safe_load(output.read_text())
    assert data["name"] == "new-feature"
    assert data["tool_permissions"]["denied"] == ["network"]

    with patch.object(sys, 'argv', ["vad", "eip", "validate", str(output)]):
        main()

def test_cli_init_refuses_overwrite_without_force(tmp_path):
    output = tmp_path / "existing.yaml"
    output.write_text("existing")

    with patch.object(sys, 'argv', ["vad", "eip", "init", "--name", "new-feature", "--out", str(output)]):
        with pytest.raises(SystemExit) as e:
            main()

    assert e.value.code == 1
    assert output.read_text() == "existing"

def test_cli_diff_reports_changed_risk_tier(tmp_path, capsys):
    old = tmp_path / "old.yaml"
    new = tmp_path / "new.yaml"
    old.write_text(VALID_EIP_YAML)
    new.write_text(VALID_EIP_YAML.replace("risk_tier: low", "risk_tier: high"))

    with patch.object(sys, 'argv', ["vad", "eip", "diff", str(old), str(new)]):
        main()

    assert "changed risk_tier: low -> high" in capsys.readouterr().out

def test_cli_diff_reports_added_proof_obligation_as_json(tmp_path, capsys):
    old = tmp_path / "old.yaml"
    new = tmp_path / "new.yaml"
    old.write_text(VALID_EIP_YAML)
    new.write_text(VALID_EIP_YAML.replace(
        "proof_obligations: []",
        """proof_obligations:
  - id: po-1
    kind: unit
    description: Unit proof"""
    ))

    with patch.object(sys, 'argv', ["vad", "eip", "diff", str(old), str(new), "--json"]):
        main()

    payload = json.loads(capsys.readouterr().out)
    assert payload["changes"][0]["kind"] == "added"
    assert payload["changes"][0]["path"] == "proof_obligations[0]"

def test_cli_diff_reports_no_changes(tmp_path, capsys):
    old = tmp_path / "old.yaml"
    new = tmp_path / "new.yaml"
    old.write_text(VALID_EIP_YAML)
    new.write_text(VALID_EIP_YAML)

    with patch.object(sys, 'argv', ["vad", "eip", "diff", str(old), str(new)]):
        main()

    assert capsys.readouterr().out == "No EIP changes.\n"

def test_cli_diff_invalid_input_exits_nonzero(tmp_path):
    old = tmp_path / "old.yaml"
    new = tmp_path / "new.yaml"
    old.write_text(VALID_EIP_YAML)
    new.write_text("name: invalid")

    with patch.object(sys, 'argv', ["vad", "eip", "diff", str(old), str(new)]):
        with pytest.raises(SystemExit) as e:
            main()

    assert e.value.code == 1

def make_run_evidence():
    return RunEvidence(
        run_id="run-1",
        created_at="2026-06-26T00:00:00",
        eip=EvidenceRef(path="eip.yaml", digest="abc"),
        proof_plan=EvidenceRef(path="proof.yaml", digest="def"),
        agents=AgentEvidence(builder="alice", verifier="bob"),
        verification=VerificationEvidence(passed=True),
        effort=EffortEvidence(
            effort_type="feature",
            mees=92,
            policy="pass",
            changed_files=1,
            line_delta=8,
            new_dependencies=0,
            complexity_delta=0,
            maintainability_delta=0,
        ),
        tokens=TokenEvidence(budget=1000, used=100),
        final_decision="passed",
    )

def test_cli_evidence_inspect_prints_summary(tmp_path, capsys):
    evidence = make_run_evidence()
    evidence_file = tmp_path / "evidence.json"
    evidence_file.write_text(json.dumps({"evidence": evidence.model_dump(mode="json")}))

    with patch.object(sys, 'argv', ["vad", "evidence", "inspect", str(evidence_file)]):
        main()

    output = capsys.readouterr().out
    assert "Decision: passed" in output
    assert "Verification: passed" in output
    assert "MEES: 92 (pass)" in output

def test_cli_evidence_inspect_tampered_hash_exits_nonzero(tmp_path):
    evidence = make_run_evidence()
    evidence_file = tmp_path / "evidence.json"
    expected_hash = EvidenceBundle(evidence).compute_hash()
    payload = evidence.model_dump(mode="json")
    payload["final_decision"] = "failed"
    evidence_file.write_text(json.dumps({"evidence": payload, "evidence_hash": expected_hash}))

    with patch.object(sys, 'argv', ["vad", "evidence", "inspect", str(evidence_file)]):
        with pytest.raises(SystemExit) as e:
            main()

    assert e.value.code == 1

def test_cli_effort_score_high_mees_passes(capsys):
    with patch("vad.effort.scoring.collect_git_diff_metrics", return_value=DiffMetrics(
        changed_files=1,
        insertions=2,
        deletions=1,
        dependency_files_changed=0,
    )):
        with patch.object(sys, 'argv', ["vad", "effort", "score", "--type", "feature"]):
            main()

    payload = json.loads(capsys.readouterr().out)
    assert payload["score"] == 96
    assert payload["policy"] == "pass"

def test_cli_effort_score_warning_threshold_exits_two():
    with patch("vad.effort.scoring.collect_git_diff_metrics", return_value=DiffMetrics(
        changed_files=5,
        insertions=15,
        deletions=15,
        dependency_files_changed=0,
    )):
        with patch.object(sys, 'argv', ["vad", "effort", "score", "--type", "feature"]):
            with pytest.raises(SystemExit) as e:
                main()

    assert e.value.code == 2

def test_cli_effort_score_fail_threshold_exits_one():
    with patch("vad.effort.scoring.collect_git_diff_metrics", return_value=DiffMetrics(
        changed_files=15,
        insertions=20,
        deletions=20,
        dependency_files_changed=1,
    )):
        with patch.object(sys, 'argv', ["vad", "effort", "score", "--type", "migration"]):
            with pytest.raises(SystemExit) as e:
                main()

    assert e.value.code == 1

def test_cli_effort_score_warn_only_suppresses_nonzero_exit(capsys):
    with patch("vad.effort.scoring.collect_git_diff_metrics", return_value=DiffMetrics(
        changed_files=15,
        insertions=20,
        deletions=20,
        dependency_files_changed=1,
    )):
        with patch.object(sys, 'argv', ["vad", "effort", "score", "--type", "migration", "--warn-only", "--readable"]):
            main()

    assert "MEES: 35 (block)" in capsys.readouterr().out
