import subprocess
from pathlib import Path
import pytest


def test_bootstrap_and_ci_verify_flow(tmp_path):
    # Setup mock structure
    mock_dir = tmp_path / "vad" / "skills" / "mock_skill"
    mock_dir.mkdir(parents=True)
    skill_file = mock_dir / "SKILL.md"
    skill_file.write_text("# Mock Skill Definition")

    reg_file = tmp_path / "registry.json"

    # 1. Run bootstrap script
    res_boot = subprocess.run(
        ["bash", "vad/integrity/bootstrap_registry.sh", str(reg_file)],
        capture_output=True,
        text=True,
    )
    assert res_boot.returncode == 0
    assert "Successfully bootstrapped" in res_boot.stdout

    # 2. Run CI verify script -> should pass
    res_ci = subprocess.run(
        ["bash", "vad/integrity/ci_verify_registry.sh", str(reg_file)],
        capture_output=True,
        text=True,
    )
    assert res_ci.returncode == 0
    assert "CI Integrity Gate Passed" in res_ci.stdout

    # 3. Tamper with file
    # Register this specific file into registry to test failure
    from vad.integrity.registry import IntegrityRegistry

    reg = IntegrityRegistry(str(reg_file))
    reg.register(str(skill_file))

    skill_file.write_text("# Malicious Modification")

    # CI verify must now fail with exit code 1
    res_ci_tampered = subprocess.run(
        ["bash", "vad/integrity/ci_verify_registry.sh", str(reg_file)],
        capture_output=True,
        text=True,
    )
    assert res_ci_tampered.returncode == 1
    assert "CI INTEGRITY VERIFICATION FAILED" in res_ci_tampered.stdout
