import json
import pytest

from vad.adapters.mcp import MCPAdapter, handle_json_rpc_request
from vad.control_plane.mcp_gateway import mcp_tool_definitions


def test_mcp_adapter_contains_work_intake_tools():
    adapter = MCPAdapter().connect()
    assert "work_intake_discover" in adapter.tools
    assert "work_intake_claim" in adapter.tools
    assert "work_intake_report_progress" in adapter.tools
    assert "work_intake_submit_proof" in adapter.tools
    assert "work_intake_escalate" in adapter.tools


def test_mcp_json_rpc_tools_list_includes_work_intake():
    req = {
        "jsonrpc": "2.0",
        "id": "req-1",
        "method": "tools/list",
        "params": {"role": "developer"},
    }
    resp = handle_json_rpc_request(req)
    assert resp is not None
    assert "result" in resp
    tools = resp["result"].get("tools", [])
    tool_names = [t["name"] for t in tools]
    assert "work_intake_discover" in tool_names
    assert "work_intake_claim" in tool_names
    assert "work_intake_report_progress" in tool_names


def test_mcp_tool_call_authorization_denied_for_unauthorized_role():
    adapter = MCPAdapter().connect()
    # High risk tools without approval require explicit approval
    res = adapter.call_tool(
        "work_intake_submit_proof",
        {
            "role": "developer",
            "work_item_id": "item-1",
            "lease_id": "lease-1",
            "evidence_digest": "a" * 64,
            "test_summary": "test",
        },
    )
    # Since high_risk=True and approved_high_risk_tools is empty, gateway denies it
    assert res.get("isError") is True
    assert "high-risk tool requires explicit approval" in res["content"][0]["text"]


def test_mcp_tool_call_authorization_allowed_with_approval():
    adapter = MCPAdapter().connect()
    # With approved_high_risk_tools parameter
    res = adapter.call_tool(
        "work_intake_submit_proof",
        {
            "role": "developer",
            "approved_high_risk_tools": ["work_intake_submit_proof"],
            "work_item_id": "item-1",
            "lease_id": "lease-1",
            "evidence_digest": "a" * 64,
            "test_summary": "test",
        },
    )
    # Authorized through gateway (even if store is not configured)
    assert "high-risk tool requires explicit approval" not in res["content"][0]["text"]
