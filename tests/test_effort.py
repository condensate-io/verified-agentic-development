import pytest
from pydantic import ValidationError

from vad.effort.scoring import (
    EffortScore,
    EffortType,
    MeesPenalties,
    collect_git_diff_metrics,
    penalties_from_diff_metrics,
    penalties_from_quality_metrics,
    quality_metrics_from_supplied,
    score_effort,
)


def test_all_effort_types_validate():
    for effort_type in EffortType:
        result = score_effort(effort_type, MeesPenalties())
        assert result.effort_type == effort_type
        assert result.score == 100
        assert result.policy == "pass"


def test_invalid_effort_type_fails():
    with pytest.raises(ValueError):
        score_effort("cleanup", MeesPenalties())


def test_score_calculation_matches_penalties():
    result = score_effort(
        "feature",
        MeesPenalties(
            complexity=4,
            maintainability=3,
            diff=5,
            spread=2,
            dependency=10,
            novelty=1,
            risk=6,
        ),
    )

    assert result.score == 69
    assert result.policy == "warn"
    assert result.penalties.total == 31


def test_score_under_50_blocks_and_requires_human():
    result = score_effort("migration", MeesPenalties(risk=30, dependency=20, diff=5))

    assert result.score == 45
    assert result.policy == "block"
    assert result.requires_human is True


def test_policy_mismatch_fails_validation():
    with pytest.raises(ValidationError):
        EffortScore(
            effort_type=EffortType.FEATURE,
            score=45,
            policy="pass",
            penalties=MeesPenalties(risk=55),
        )


def test_no_tool_fallback_is_explicit_warning_not_fake_metric():
    result = score_effort("test")

    assert result.score == 100
    assert result.warnings == ["MEES scored from explicit zero-penalty fallback; no metric tool output supplied"]


class FakeCompletedProcess:
    def __init__(self, stdout="", returncode=0):
        self.stdout = stdout
        self.returncode = returncode


def test_collect_git_diff_metrics_counts_changed_files_and_lines():
    calls = []

    def runner(command, **kwargs):
        calls.append(command)
        if "--numstat" in command:
            return FakeCompletedProcess("3\t2\tvad/file.py\n1\t0\ttests/test_file.py\n")
        return FakeCompletedProcess("vad/file.py\ntests/test_file.py\n")

    metrics = collect_git_diff_metrics(runner=runner)

    assert metrics.changed_files == 2
    assert metrics.insertions == 4
    assert metrics.deletions == 2
    assert metrics.line_delta == 6
    assert calls[0][:3] == ["git", "diff", "--numstat"]


def test_dependency_manifest_change_increases_dependency_penalty():
    def runner(command, **kwargs):
        if "--numstat" in command:
            return FakeCompletedProcess("1\t1\tpyproject.toml\n2\t0\tvad/file.py\n")
        return FakeCompletedProcess("pyproject.toml\nvad/file.py\n")

    metrics = collect_git_diff_metrics(runner=runner)
    penalties = penalties_from_diff_metrics(metrics)

    assert metrics.dependency_files_changed == 1
    assert penalties.dependency == 10
    assert penalties.diff == 4
    assert penalties.spread == 2


def test_missing_git_returns_controlled_fallback():
    def runner(command, **kwargs):
        raise FileNotFoundError("git")

    metrics = collect_git_diff_metrics(runner=runner)

    assert metrics.changed_files == 0
    assert metrics.warnings == ["git unavailable"]


def test_git_error_returns_controlled_fallback():
    def runner(command, **kwargs):
        return FakeCompletedProcess(returncode=128)

    metrics = collect_git_diff_metrics(runner=runner)

    assert metrics.changed_files == 0
    assert metrics.warnings == ["git diff metrics unavailable"]


def test_quality_metrics_affect_complexity_and_maintainability_penalties():
    metrics = quality_metrics_from_supplied(
        cyclomatic_delta=3,
        cognitive_delta=4,
        maintainability_delta=-6,
    )
    penalties = penalties_from_quality_metrics(metrics)

    assert penalties.complexity == 7
    assert penalties.maintainability == 6


def test_quality_metrics_fallback_preserves_unknowns_with_warnings():
    metrics = quality_metrics_from_supplied()
    penalties = penalties_from_quality_metrics(metrics)

    assert penalties.complexity == 0
    assert penalties.maintainability == 0
    assert metrics.warnings == [
        "cyclomatic complexity metric unavailable",
        "cognitive complexity metric unavailable",
        "maintainability metric unavailable",
    ]
