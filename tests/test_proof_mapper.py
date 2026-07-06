import sys
from unittest.mock import patch

import pytest
import yaml
from pydantic import ValidationError

from vad.cli import main
from vad.contracts.models import EIP
from vad.proof.plan import ProofMapping, ProofPlan, compute_eip_digest
from vad.proof.mapper import map_proofs


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
    purpose: proof mapping
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


EIP_WITH_PROOFS = VALID_EIP_YAML.replace(
    "proof_obligations: []",
    """proof_obligations:
  - id: po-2
    kind: property
    description: Property proof
  - id: po-1
    kind: unit
    description: Unit proof"""
)


def test_map_proofs_is_deterministic():
    eip = EIP(**yaml.safe_load(EIP_WITH_PROOFS))

    first = map_proofs(eip).model_dump(mode="json")
    second = map_proofs(eip).model_dump(mode="json")

    assert first == second
    assert [item["obligation_id"] for item in first["mappings"]] == ["po-1", "po-2"]


def test_map_proofs_fails_without_coverage():
    eip = EIP(**yaml.safe_load(VALID_EIP_YAML))

    with pytest.raises(ValueError, match="no proof obligations"):
        map_proofs(eip)


def test_cli_proof_map_writes_valid_plan(tmp_path):
    eip_file = tmp_path / "eip.yaml"
    proof_file = tmp_path / "proof.yaml"
    eip_file.write_text(EIP_WITH_PROOFS)

    with patch.object(sys, "argv", ["vad", "proof", "map", str(eip_file), "--out", str(proof_file)]):
        main()

    plan = yaml.safe_load(proof_file.read_text())
    assert plan["eip_version"] == "1.0.0"
    assert plan["schema_version"] == "1.0.0"
    assert plan["eip_digest"]
    assert plan["mappings"][0]["obligation_id"] == "po-1"


def test_cli_proof_map_missing_coverage_exits_nonzero(tmp_path):
    eip_file = tmp_path / "eip.yaml"
    eip_file.write_text(VALID_EIP_YAML)

    with patch.object(sys, "argv", ["vad", "proof", "map", str(eip_file)]):
        with pytest.raises(SystemExit) as e:
            main()

    assert e.value.code == 1

def test_proof_plan_rejects_duplicate_obligation_mapping():
    eip = EIP(**yaml.safe_load(EIP_WITH_PROOFS))

    with pytest.raises(ValidationError, match="duplicate"):
        ProofPlan(
            eip_version=eip.version,
            eip_digest=compute_eip_digest(eip),
            mappings=[
                ProofMapping(obligation_id="po-1", test_command="pytest"),
                ProofMapping(obligation_id="po-1", test_command="pytest"),
            ],
        )
