import json
import sys
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from vad.ask.assessment import AskAssessment
from vad.ask.assessment import eip_from_assessment
from vad.ask.classifier import assess_ask
from vad.cli import main
from vad.contracts.models import AutonomyTier, RiskTier


def test_assessment_model_serializes_and_rejects_invalid_effort_type():
    assessment = assess_ask("Add a feature in vad/cli.py so that users can validate input.")
    payload = assessment.model_dump(mode="json")

    assert AskAssessment(**payload) == assessment

    payload["effort_type"] = "unknown"
    with pytest.raises(ValidationError):
        AskAssessment(**payload)


def test_clear_low_risk_ask_allows_assisted_work():
    assessment = assess_ask("Add a CLI flag in vad/cli.py so that output can be JSON.")

    assert assessment.risk_tier == RiskTier.LOW
    assert assessment.autonomy_tier == AutonomyTier.ASSISTED
    assert assessment.effort_type == "feature"
    assert assessment.clarification_questions == []
    assert "unit" in assessment.required_proof_kinds


def test_ambiguous_ask_blocks_autonomous_execution():
    assessment = assess_ask("Improve this somehow, maybe whatever is best.")

    assert assessment.blocks_autonomous_execution is True
    assert assessment.ambiguity_score > 0
    assert assessment.clarification_questions


def test_security_sensitive_ask_requires_manual_autonomy():
    assessment = assess_ask("Fix the auth bypass in production so that private data is protected.")

    assert assessment.risk_tier == RiskTier.HIGH
    assert assessment.autonomy_tier == AutonomyTier.MANUAL
    assert "security" in assessment.required_proof_kinds
    assert assessment.mees_budget.requires_justification is True
    assert assessment.token_budget == 8000


def test_dependency_adding_ask_requires_approval_budget():
    assessment = assess_ask("Add a new API integration in service module so that data sync succeeds.")

    assert assessment.risk_tier == RiskTier.MEDIUM
    assert assessment.mees_budget.allow_new_dependencies is False


def test_cli_ask_assess_writes_json(tmp_path):
    ask_file = tmp_path / "ask.txt"
    out_file = tmp_path / "assessment.json"
    ask_file.write_text("Add a CLI flag in vad/cli.py so that output can be JSON.")

    with patch.object(sys, "argv", ["vad", "ask", "assess", str(ask_file), "--out", str(out_file)]):
        main()

    payload = json.loads(out_file.read_text())
    assert payload["risk_tier"] == "low"
    assert payload["effort_type"] == "feature"
    assert payload["token_budget"] == 10000

def test_assessment_to_eip_draft_validates_and_preserves_manual_gate():
    assessment = assess_ask("Fix the auth bypass in production so that private data is protected.")

    eip = eip_from_assessment("auth-fix", assessment)

    assert eip.name == "auth-fix"
    assert eip.risk_tier == RiskTier.HIGH
    assert eip.autonomy_tier == AutonomyTier.MANUAL
    assert eip.model_budget.max_tokens == assessment.token_budget
    assert any(ob.kind == "manual_gate" for ob in eip.proof_obligations)

def test_cli_init_from_assessment_creates_valid_eip(tmp_path):
    ask_file = tmp_path / "ask.txt"
    assessment_file = tmp_path / "assessment.json"
    eip_file = tmp_path / "eip.yaml"
    ask_file.write_text("Add a CLI flag in vad/cli.py so that output can be JSON.")

    with patch.object(sys, "argv", ["vad", "ask", "assess", str(ask_file), "--out", str(assessment_file)]):
        main()

    with patch.object(
        sys,
        "argv",
        ["vad", "eip", "init", "--name", "json-output", "--from-assessment", str(assessment_file), "--out", str(eip_file)],
    ):
        main()

    with patch.object(sys, "argv", ["vad", "eip", "validate", str(eip_file)]):
        main()

    data = json.loads(assessment_file.read_text())
    eip_data = __import__("yaml").safe_load(eip_file.read_text())
    assert eip_data["name"] == "json-output"
    assert eip_data["model_budget"]["max_tokens"] == data["token_budget"]
