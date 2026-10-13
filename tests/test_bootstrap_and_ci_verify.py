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

    posix_reg = reg_file.as_posix()
    posix_skill = skill_file.as_posix()

    # 1. Run bootstrap script
    res_boot = subprocess.run(
        ["bash", "vad/integrity/bootstrap_registry.sh", posix_reg],
        capture_output=True,
        text=True,
    )
    assert res_boot.returncode == 0
    assert "Successfully bootstrapped" in res_boot.stdout

    # 2. Run CI verify script -> should pass
    res_ci = subprocess.run(
        ["bash", "vad/integrity/ci_verify_registry.sh", posix_reg],
        capture_output=True,
        text=True,
    )
    assert res_ci.returncode == 0
    assert "CI Integrity Gate Passed" in res_ci.stdout

    # 3. Tamper with file
    from vad.integrity.registry import IntegrityRegistry

    reg = IntegrityRegistry(posix_reg)
    reg.register(posix_skill)

    skill_file.write_text("# Malicious Modification")

    # CI verify must now fail with exit code 1
    res_ci_tampered = subprocess.run(
        ["bash", "vad/integrity/ci_verify_registry.sh", posix_reg],
        capture_output=True,
        text=True,
    )
    assert res_ci_tampered.returncode == 1
    assert "CI INTEGRITY VERIFICATION FAILED" in res_ci_tampered.stdout

    # 4. Test REJECT verdict flagging
    skill_file.write_text("# Valid again")
    reg.register(posix_skill, verdict="REJECT", report="Contains suspicious eval()")
    res_ci_reject = subprocess.run(
        ["bash", "vad/integrity/ci_verify_registry.sh", posix_reg],
        capture_output=True,
        text=True,
    )
    assert res_ci_reject.returncode == 1
    assert "REJECTED ARTIFACTS IN REGISTRY" in res_ci_reject.stdout

    # 5. Test BOOTSTRAP verdict flagging
    reg.register(posix_skill, verdict="BOOTSTRAP", report="Unreviewed baseline")
    res_ci_bootstrap = subprocess.run(
        ["bash", "vad/integrity/ci_verify_registry.sh", posix_reg],
        capture_output=True,
        text=True,
    )
    assert res_ci_bootstrap.returncode == 1
    assert "UNREVIEWED BOOTSTRAP-ONLY ARTIFACTS" in res_ci_bootstrap.stdout
