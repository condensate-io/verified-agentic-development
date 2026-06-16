import io
import json
import sys
import pytest
from unittest.mock import patch, MagicMock
from vad.adapters.mcp import serve

def test_mcp_initialize():
    req = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "test-client", "version": "1.0"}
        }
    }
    
    stdin_mock = io.StringIO(json.dumps(req) + "\n")
    stdout_mock = io.StringIO()
    
    with patch('sys.stdin', stdin_mock), patch('sys.stdout', stdout_mock), patch('sys.stderr', MagicMock()):
        serve()
        
    stdout_mock.seek(0)
    response_line = stdout_mock.readline()
    assert response_line != ""
    
    resp = json.loads(response_line)
    assert resp["jsonrpc"] == "2.0"
    assert resp["id"] == 1
    assert "result" in resp
    assert resp["result"]["protocolVersion"] == "2024-11-05"
    assert "tools" in resp["result"]["capabilities"]

def test_mcp_tools_list():
    req = {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/list",
        "params": {}
    }
    
    stdin_mock = io.StringIO(json.dumps(req) + "\n")
    stdout_mock = io.StringIO()
    
    with patch('sys.stdin', stdin_mock), patch('sys.stdout', stdout_mock), patch('sys.stderr', MagicMock()):
        serve()
        
    stdout_mock.seek(0)
    response_line = stdout_mock.readline()
    resp = json.loads(response_line)
    assert resp["id"] == 2
    assert "tools" in resp["result"]
    tools = resp["result"]["tools"]
    tool_names = [t["name"] for t in tools]
    assert "validate_eip" in tool_names
    assert "run_proofs" in tool_names
    assert "retro_analyze" in tool_names
    assert "query_retrospective" in tool_names
    assert "submit_retrospective" in tool_names

def test_mcp_validate_eip(tmp_path):
    eip_file = tmp_path / "eip.yaml"
    eip_data = {
        "version": "1.0.0",
        "name": "test-eip",
        "risk_tier": "low",
        "goal": {
            "description": "Test EIP goal",
            "success_criteria": ["All tests pass"]
        },
        "invariants": {
            "functional": ["System remains operational"]
        },
        "proof_obligations": [
            {
                "id": "PO-1",
                "kind": "unit_test",
                "description": "Verify system is operational"
            }
        ]
    }
    import yaml
    with open(eip_file, "w") as f:
        yaml.dump(eip_data, f)
        
    req = {
        "jsonrpc": "2.0",
        "id": 3,
        "method": "tools/call",
        "params": {
            "name": "validate_eip",
            "arguments": {"file": str(eip_file)}
        }
    }
    
    stdin_mock = io.StringIO(json.dumps(req) + "\n")
    stdout_mock = io.StringIO()
    
    with patch('sys.stdin', stdin_mock), patch('sys.stdout', stdout_mock), patch('sys.stderr', MagicMock()):
        serve()
        
    stdout_mock.seek(0)
    response_line = stdout_mock.readline()
    resp = json.loads(response_line)
    assert resp["id"] == 3
    assert "result" in resp
    assert "Validation successful" in resp["result"]["content"][0]["text"]
