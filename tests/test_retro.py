import pytest
import json
import sys
import subprocess
from vad.evidence.bundle import EvidenceBundle
from vad.feedback.retro import RetroAnalyzer
from vad.memory.gateway import MemoryGateway
from vad.memory.stores.local import LocalMemoryStore
from vad.memory.redaction import Redactor
from vad.memory.contracts import MemoryScope

def test_retro_analyzer_extracts_failures():
    bundle_data = {
        "failures": ["Top level policy denial"],
        "steps": [
            {
                "status": "success",
                "output": "all good"
            },
            {
                "status": "failure",
                "error": "Validation error in step 2",
                "policy_denial": "Policy X violated",
                "loop_exhaustion": "Exhausted 5 loops"
            }
        ]
    }
    bundle = EvidenceBundle(bundle_data)
    store = LocalMemoryStore()
    gateway = MemoryGateway(store, Redactor())
    analyzer = RetroAnalyzer(gateway)
    
    result = analyzer.analyze(bundle)
    
    assert "failures" in result
    failures = result["failures"]
    assert len(failures) == 4
    assert "Top level policy denial" in failures
    assert "Validation error in step 2" in failures
    assert "Policy X violated" in failures
    assert "Exhausted 5 loops" in failures
    
    # Check if written to memory
    entries = store.get_by_scope(MemoryScope.RETROSPECTIVE)
    assert len(entries) == 1
    
    content = json.loads(entries[0].content)
    assert len(content["failures"]) == 4

def test_cli_retro_command(tmp_path):
    bundle_data = {
        "failures": ["Initial test failure"],
        "steps": []
    }
    
    file_path = tmp_path / "bundle.json"
    with open(file_path, "w") as f:
        json.dump(bundle_data, f)
        
    cmd = [sys.executable, "-m", "vad.cli", "eip", "retro", str(file_path)]
    result = subprocess.run(cmd, capture_output=True, text=True)
    
    assert result.returncode == 0
    assert "Retro analysis complete." in result.stdout
    assert "Initial test failure" in result.stdout
