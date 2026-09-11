import json
import pytest
from pathlib import Path
from vad.plugins.claude_code.mcp_integration import VADMCPServer


def test_mcp_server_tools_list():
    server = VADMCPServer()
    req = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    resp = server.handle_jsonrpc(req)

    assert resp["jsonrpc"] == "2.0"
    assert resp["id"] == 1
    tool_names = [t["name"] for t in resp["result"]["tools"]]
    assert "vad_assess" in tool_names
    assert "vad_proof_map" in tool_names
    assert "vad_evidence_inspect" in tool_names
    assert "vad_release_gate" in tool_names


def test_mcp_server_vad_assess():
    server = VADMCPServer()
    req = {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/call",
        "params": {
            "name": "vad_assess",
            "arguments": {
                "goal_description": "Implement authentication token rotation",
                "success_criteria": ["All tokens expire safely"],
            },
        },
    }
    resp = server.handle_jsonrpc(req)
    assert resp["result"]["isError"] is False
    content_text = resp["result"]["content"][0]["text"]
    data = json.loads(content_text)
    assert data["status"] == "admit"
    assert data["admitted"] is True


def test_mcp_server_vad_proof_map():
    server = VADMCPServer()
    req = {
        "jsonrpc": "2.0",
        "id": 3,
        "method": "tools/call",
        "params": {
            "name": "vad_proof_map",
            "arguments": {"eip_id": "eip-123"},
        },
    }
    resp = server.handle_jsonrpc(req)
    assert resp["result"]["isError"] is False
    data = json.loads(resp["result"]["content"][0]["text"])
    assert data["total"] == 3


def test_mcp_server_vad_evidence_inspect(tmp_path):
    ev_dir = tmp_path / "evidence"
    ev_dir.mkdir()
    (ev_dir / "item1.json").write_text('{"id": "item1", "status": "passed"}')

    server = VADMCPServer(workspace_root=str(tmp_path))
    req = {
        "jsonrpc": "2.0",
        "id": 4,
        "method": "tools/call",
        "params": {
            "name": "vad_evidence_inspect",
            "arguments": {"item_id": "item1"},
        },
    }
    resp = server.handle_jsonrpc(req)
    data = json.loads(resp["result"]["content"][0]["text"])
    assert data["count"] == 1
    assert data["evidence"][0]["id"] == "item1"


def test_mcp_server_vad_release_gate():
    server = VADMCPServer()
    req = {
        "jsonrpc": "2.0",
        "id": 5,
        "method": "tools/call",
        "params": {
            "name": "vad_release_gate",
            "arguments": {
                "artifact_id": "art-prod",
                "has_attestation": True,
                "test_coverage": 92.5,
            },
        },
    }
    resp = server.handle_jsonrpc(req)
    data = json.loads(resp["result"]["content"][0]["text"])
    assert data["release_admitted"] is True
    assert data["checks"]["dsse_attestation"] == "passed"
