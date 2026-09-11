from vad.control_plane.mcp_gateway import GatewayToolRisk
from vad.control_plane.mcp_work_tools import get_work_intake_tool_definitions


def test_work_intake_tool_definitions_presence():
    tools = get_work_intake_tool_definitions()
    expected = [
        "work_intake_discover",
        "work_intake_claim",
        "work_intake_report_progress",
        "work_intake_submit_proof",
        "work_intake_escalate",
    ]
    for tool_name in expected:
        assert tool_name in tools
        t = tools[tool_name]
        assert t.name == tool_name
        assert len(t.description) > 10
        assert t.required_role == "developer"


def test_tool_risk_tiers():
    tools = get_work_intake_tool_definitions()
    assert tools["work_intake_discover"].risk == GatewayToolRisk.LOW
    assert tools["work_intake_report_progress"].risk == GatewayToolRisk.LOW
    assert tools["work_intake_claim"].risk == GatewayToolRisk.MEDIUM
    assert tools["work_intake_submit_proof"].risk == GatewayToolRisk.HIGH
    assert tools["work_intake_submit_proof"].high_risk is True
    assert tools["work_intake_escalate"].risk == GatewayToolRisk.HIGH
    assert tools["work_intake_escalate"].high_risk is True


def test_to_mcp_tool_schema_export():
    tools = get_work_intake_tool_definitions()
    claim_mcp = tools["work_intake_claim"].to_mcp_tool()
    assert claim_mcp["name"] == "work_intake_claim"
    assert "inputSchema" in claim_mcp
    assert "work_item_id" in claim_mcp["inputSchema"]["properties"]
    assert "client_id" in claim_mcp["inputSchema"]["required"]
