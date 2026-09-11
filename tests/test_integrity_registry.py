import pytest
from vad.integrity.registry import IntegrityRegistry, IntegrityViolation


def test_integrity_registry_registration_and_verification(tmp_path):
    reg_file = tmp_path / "registry.json"
    registry = IntegrityRegistry(str(reg_file))

    # Create a skill file
    skill_file = tmp_path / "SKILL.md"
    skill_file.write_text("# Production Telephony Skill\nInstructions here.")

    # Register
    entry = registry.register(str(skill_file), category="skill")
    assert entry.sha256 is not None
    assert len(entry.sha256) == 64

    # Verify matching file
    assert registry.verify(str(skill_file)) is True

    # Tamper with file
    skill_file.write_text("# Tampered Malicious Payload")
    with pytest.raises(IntegrityViolation, match="Integrity violation"):
        registry.verify(str(skill_file))


def test_integrity_registry_unregistered_file(tmp_path):
    reg_file = tmp_path / "registry.json"
    registry = IntegrityRegistry(str(reg_file))

    unregistered = tmp_path / "rogue_script.sh"
    unregistered.write_text("echo rogue")

    valid, err = registry.verify_safe(str(unregistered))
    assert valid is False
    assert "Unregistered artifact" in err
