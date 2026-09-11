import pytest
import yaml
from pathlib import Path
from vad.onboarding.descriptor_generator import DescriptorGenerator, RepoDescriptor


def test_descriptor_generator_from_path(tmp_path):
    repo_dir = tmp_path / "voice-sip-service"
    repo_dir.mkdir()
    readme = repo_dir / "README.md"
    readme.write_text("# Voice SIP Service\nHigh performance audio streaming and SIP signaling gateway.", encoding="utf-8")
    (repo_dir / "pyproject.toml").write_text("[project]\nname = 'voice-sip'", encoding="utf-8")
    (repo_dir / "main.py").write_text("print('sip')", encoding="utf-8")

    generator = DescriptorGenerator()
    desc = generator.generate_from_path(repo_dir)

    assert isinstance(desc, RepoDescriptor)
    assert desc.repo_name == "voice-sip-service"
    assert desc.language == "python"
    assert "audio" in desc.routing_signals
    assert "sip" in desc.routing_signals
    assert "main.py" in desc.entrypoints
    assert "README.md" in desc.key_files

    out_yaml = tmp_path / "corpus" / "voice-sip.yaml"
    generator.save_descriptor(desc, out_yaml)
    assert out_yaml.exists()

    loaded = yaml.safe_load(out_yaml.read_text(encoding="utf-8"))
    assert loaded["repo_name"] == "voice-sip-service"
