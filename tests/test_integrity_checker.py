import pytest
from vad.integrity.integrity_checker import RuntimeIntegrityChecker
from vad.integrity.registry import IntegrityRegistry


def test_integrity_checker_allows_valid_file(tmp_path):
    reg_path = tmp_path / "registry.json"
    reg = IntegrityRegistry(str(reg_path))

    script = tmp_path / "deploy_hook.sh"
    script.write_text("#!/bin/bash\necho 'safe'")
    reg.register(str(script), category="hook")

    checker = RuntimeIntegrityChecker(registry=reg, enforce=True)
    res = checker.check(str(script))
    assert res.decision == "allow"
    assert res.is_verified is True


def test_integrity_checker_blocks_tampered_file(tmp_path):
    reg_path = tmp_path / "registry.json"
    reg = IntegrityRegistry(str(reg_path))

    script = tmp_path / "skill_action.sh"
    script.write_text("#!/bin/bash\necho 'valid'")
    reg.register(str(script), category="skill")

    # Modify file without updating registry
    script.write_text("#!/bin/bash\necho 'compromised'")

    checker = RuntimeIntegrityChecker(registry=reg, enforce=True)
    res = checker.check(str(script))
    assert res.decision == "block"
    assert res.is_verified is False
    assert "Integrity violation" in res.reason


def test_integrity_checker_inspects_command_args(tmp_path):
    reg_path = tmp_path / "registry.json"
    reg = IntegrityRegistry(str(reg_path))

    script = tmp_path / "run_test.py"
    script.write_text("print('test')")
    reg.register(str(script))

    checker = RuntimeIntegrityChecker(registry=reg, enforce=True)
    results = checker.check_command_paths(["python", str(script)])
    assert len(results) == 1
    assert results[0].decision == "allow"
