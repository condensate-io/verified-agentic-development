import pytest
import yaml
from pathlib import Path


def test_post_mortem_schema_valid_yaml():
    schema_path = Path("schemas/post_mortem.yaml")
    assert schema_path.exists()
    content = schema_path.read_text(encoding="utf-8")
    schema = yaml.safe_load(content)

    assert schema["title"] == "PostMortem"
    assert "required" in schema
    assert "id" in schema["required"]
    assert "proposed_eip_clauses" in schema["required"]
    assert "severity" in schema["properties"]
    assert "SEV-1" in schema["properties"]["severity"]["enum"]
