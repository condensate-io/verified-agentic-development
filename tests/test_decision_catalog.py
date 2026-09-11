import pytest
from vad.decision.catalog import DecisionCatalog, DecisionEntry, DecisionOutcome, DecisionCategory


def test_decision_catalog_registration_and_lookup():
    catalog = DecisionCatalog()
    entry = DecisionEntry(
        id="test-dec-1",
        category=DecisionCategory.TOOL_EXECUTION.value,
        title="Tool Permission Gate",
        description="Check if tool is permissible",
        outcomes=[
            DecisionOutcome(name="allow", description="Execute tool"),
            DecisionOutcome(name="deny", description="Block tool"),
        ],
        rationale_template="Tool {tool} was evaluated: {decision}",
        required_evidence_fields=["tool", "timestamp"],
    )
    catalog.register(entry)

    retrieved = catalog.get("test-dec-1")
    assert retrieved is not None
    assert retrieved.title == "Tool Permission Gate"
    assert len(retrieved.outcomes) == 2


def test_decision_catalog_yaml_roundtrip(tmp_path):
    catalog = DecisionCatalog.default_catalog()
    assert catalog.get("dec-dor-admit") is not None

    catalog_file = tmp_path / "decisions.yaml"
    catalog.save_file(str(catalog_file))

    loaded = DecisionCatalog.load_file(str(catalog_file))
    assert loaded.get("dec-dor-admit") is not None
    assert loaded.get("dec-release-gate") is not None
    assert loaded.get("dec-dor-admit").required_evidence_fields == ["eip_digest", "evaluation_result", "evaluated_by"]


def test_decision_catalog_filter_by_category():
    catalog = DecisionCatalog.default_catalog()
    admission_decisions = catalog.list_by_category(DecisionCategory.ADMISSION.value)
    assert len(admission_decisions) == 1
    assert admission_decisions[0].id == "dec-dor-admit"
