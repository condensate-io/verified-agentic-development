from vad.policy.engine import PolicyEngine
from vad.repo.dependencies import assess_dependency_changes, dependency_ecosystem


def test_detects_python_node_and_go_dependency_manifests():
    assessment = assess_dependency_changes([
        "pyproject.toml",
        "web/package.json",
        "service/go.mod",
        "src/app.py",
    ])

    assert [change.ecosystem for change in assessment.changes] == ["python", "node", "go"]
    assert assessment.has_dependency_changes is True


def test_dependency_change_without_approval_blocks():
    assessment = assess_dependency_changes(["requirements.txt"], approved=False)

    decision = assessment.decision()

    assert decision.allow is False
    assert decision.requires_human is True
    assert "explicit approval" in decision.denials[0]


def test_approved_dependency_change_is_recorded_as_allowed():
    assessment = assess_dependency_changes(["package-lock.json"], approved=True)

    decision = assessment.decision()

    assert decision.allow is True
    assert assessment.changes[0].path == "package-lock.json"


def test_policy_engine_dependency_change_gate_matches_assessment():
    decision = PolicyEngine().evaluate_dependency_change(has_dependency_changes=True, approved=False)

    assert decision.allow is False
    assert decision.requires_human is True


def test_non_dependency_file_has_no_ecosystem():
    assert dependency_ecosystem("src/app.py") is None
