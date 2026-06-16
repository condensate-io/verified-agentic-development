import sys
import pytest
from unittest.mock import patch
from vad.cli import main
from pathlib import Path

def test_cli_validate_valid_yaml(tmp_path):
    sample_yaml = tmp_path / "sample.yaml"
    sample_yaml.write_text('''
version: 1.0.0
name: Sample
risk_tier: low
goal:
  description: Test
  success_criteria: ["Pass"]
invariants: {}
proof_obligations: []
''')
    
    test_args = ["vad", "eip", "validate", str(sample_yaml)]
    with patch.object(sys, 'argv', test_args):
        try:
            main()
        except SystemExit as e:
            assert e.code == 0

def test_cli_validate_invalid_yaml(tmp_path):
    sample_yaml = tmp_path / "invalid.yaml"
    sample_yaml.write_text('''
version: 1.0.0
name: Sample
risk_tier: invalid_tier
goal:
  description: Test
  success_criteria: ["Pass"]
invariants: {}
proof_obligations: []
''')
    
    test_args = ["vad", "eip", "validate", str(sample_yaml)]
    with patch.object(sys, 'argv', test_args):
        with pytest.raises(SystemExit) as e:
            main()
        assert e.value.code == 1
