from __future__ import annotations

from typing import Dict, List

from vad.control_plane.mcp_gateway import (
    GatewayToolDefinition,
    GatewayToolEventPolicy,
    GatewayToolRisk,
)


def get_work_intake_tool_definitions() -> Dict[str, GatewayToolDefinition]:
    """Returns the standardized MCP tool definitions for governed work intake."""
    return {
        "work_intake_discover": GatewayToolDefinition(
            name="work_intake_discover",
            description="Discovers available or queued work items matching agent role and capability requirements.",
            risk=GatewayToolRisk.LOW,
            required_role="developer",
            high_risk=False,
            input_schema={
                "type": "object",
                "properties": {
                    "role": {
                        "type": "string",
                        "description": "Agent role to match (e.g. 'developer', 'qa', 'architect')",
                        "default": "developer",
                    },
                    "capability": {
                        "type": "string",
                        "description": "Optional specific capability required for work items",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of items to return",
                        "default": 10,
                    },
                },
            },
        ),
        "work_intake_claim": GatewayToolDefinition(
            name="work_intake_claim",
            description="Acquires an execution lease on a queued work item for the agent.",
            risk=GatewayToolRisk.MEDIUM,
            required_role="developer",
            high_risk=False,
            input_schema={
                "type": "object",
                "properties": {
                    "work_item_id": {
                        "type": "string",
                        "description": "Identifier of the work item to claim",
                    },
                    "client_id": {
                        "type": "string",
                        "description": "Agent client identifier",
                    },
                    "lease_duration_seconds": {
                        "type": "integer",
                        "description": "Lease duration in seconds (default: 600)",
                        "default": 600,
                    },
                },
                "required": ["work_item_id", "client_id"],
            },
        ),
        "work_intake_report_progress": GatewayToolDefinition(
            name="work_intake_report_progress",
            description="Reports interim progress, milestone status, or completion percentage on a claimed work item.",
            risk=GatewayToolRisk.LOW,
            required_role="developer",
            high_risk=False,
            input_schema={
                "type": "object",
                "properties": {
                    "work_item_id": {
                        "type": "string",
                        "description": "Identifier of the claimed work item",
                    },
                    "lease_id": {
                        "type": "string",
                        "description": "Active lease identifier for the work item",
                    },
                    "message": {
                        "type": "string",
                        "description": "Progress note or status explanation",
                    },
                    "percent_complete": {
                        "type": "integer",
                        "description": "Estimated percent complete (0-100)",
                    },
                },
                "required": ["work_item_id", "lease_id", "message"],
            },
        ),
        "work_intake_submit_proof": GatewayToolDefinition(
            name="work_intake_submit_proof",
            description="Submits verification proof digest and test summary, transitioning work item to verifying state.",
            risk=GatewayToolRisk.HIGH,
            required_role="developer",
            high_risk=True,
            input_schema={
                "type": "object",
                "properties": {
                    "work_item_id": {
                        "type": "string",
                        "description": "Identifier of the work item",
                    },
                    "lease_id": {
                        "type": "string",
                        "description": "Active lease identifier",
                    },
                    "evidence_digest": {
                        "type": "string",
                        "description": "64-character SHA-256 evidence digest",
                    },
                    "test_summary": {
                        "type": "string",
                        "description": "Summary of executed tests and verification outcomes",
                    },
                    "commit_sha": {
                        "type": "string",
                        "description": "Optional Git commit SHA associated with the proof",
                    },
                },
                "required": ["work_item_id", "lease_id", "evidence_digest", "test_summary"],
            },
        ),
        "work_intake_escalate": GatewayToolDefinition(
            name="work_intake_escalate",
            description="Escalates a blocker or requests human intervention on a claimed work item.",
            risk=GatewayToolRisk.HIGH,
            required_role="developer",
            high_risk=True,
            input_schema={
                "type": "object",
                "properties": {
                    "work_item_id": {
                        "type": "string",
                        "description": "Identifier of the work item",
                    },
                    "lease_id": {
                        "type": "string",
                        "description": "Active lease identifier",
                    },
                    "reason": {
                        "type": "string",
                        "description": "Explanation of the blocking issue or required human decision",
                    },
                },
                "required": ["work_item_id", "lease_id", "reason"],
            },
        ),
    }
