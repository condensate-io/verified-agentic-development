from pathlib import Path
import pytest

from vad.effort.scoring import MeesBudget, evaluate_mees_policy


def test_mees_rego_file_content():
    rego_path = Path("policies/mees.rego")
    assert rego_path.exists()
    content = rego_path.read_text(encoding="utf-8")
    assert "package vad.minimal_effort" in content
    assert "default allow = false" in content
    assert "default warn = false" in content
    assert "input.mees >= 70" in content
    assert "input.mees >= 50" in content
    assert "input.mees < 50" in content
    assert "max_changed_files + 3" in content
    assert "max_complexity_increase + 2" in content
    assert "not input.budget.allow_new_dependencies" in content


def test_mees_policy_allow_tier():
    input_data = {
        "mees": 85,
        "changed_files": 4,
        "complexity_delta": 2,
        "new_dependencies": 0,
        "budget": {
            "max_changed_files": 10,
            "max_complexity_increase": 5,
            "allow_new_dependencies": False,
        },
    }
    res = evaluate_mees_policy(input_data)
    assert res.allow is True
    assert res.warn is False
    assert len(res.deny_reasons) == 0


def test_mees_policy_warn_tier_score():
    # MEES between 50 and 69 triggers warn
    input_data = {
        "mees": 62,
        "changed_files": 4,
        "complexity_delta": 2,
        "new_dependencies": 0,
        "budget": {
            "max_changed_files": 10,
            "max_complexity_increase": 5,
            "allow_new_dependencies": False,
        },
    }
    res = evaluate_mees_policy(input_data)
    assert res.allow is False
    assert res.warn is True
    assert len(res.deny_reasons) == 0


def test_mees_policy_warn_tier_files_tolerance():
    # changed_files 12 > 10 but <= 10 + 3 -> warn
    input_data = {
        "mees": 75,
        "changed_files": 12,
        "complexity_delta": 3,
        "new_dependencies": 0,
        "budget": {
            "max_changed_files": 10,
            "max_complexity_increase": 5,
            "allow_new_dependencies": False,
        },
    }
    res = evaluate_mees_policy(input_data)
    assert res.allow is False
    assert res.warn is True
    assert len(res.deny_reasons) == 0


def test_mees_policy_deny_score():
    # MEES < 50 triggers deny
    input_data = {
        "mees": 42,
        "changed_files": 2,
        "complexity_delta": 1,
        "new_dependencies": 0,
        "budget": {
            "max_changed_files": 10,
            "max_complexity_increase": 5,
            "allow_new_dependencies": False,
        },
    }
    res = evaluate_mees_policy(input_data)
    assert res.allow is False
    assert "MEES score 42 is below minimum threshold (50)" in res.deny_reasons


def test_mees_policy_deny_files_and_complexity_and_deps():
    # Multiple deny reasons
    input_data = {
        "mees": 40,
        "changed_files": 15,
        "complexity_delta": 9,
        "new_dependencies": 2,
        "budget": {
            "max_changed_files": 10,
            "max_complexity_increase": 5,
            "allow_new_dependencies": False,
        },
    }
    res = evaluate_mees_policy(input_data)
    assert res.allow is False
    assert len(res.deny_reasons) == 4
    assert any("below minimum threshold (50)" in r for r in res.deny_reasons)
    assert any("exceeds budget (10) + tolerance (3)" in r for r in res.deny_reasons)
    assert any("exceeds budget (5) + tolerance (2)" in r for r in res.deny_reasons)
    assert any("disallows new deps" in r for r in res.deny_reasons)
