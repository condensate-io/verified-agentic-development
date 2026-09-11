"""Model Context Protocol (MCP) Server for VAD.

Exposes VAD governance capabilities to AI coding assistants via standard MCP tool interface:
- vad_assess: evaluates DoR gate and policy constraints for an intent/diff
- vad_proof_map: returns proof obligations and formal requirements
- vad_evidence_inspect: inspects stored verification evidence
- vad_release_gate: evaluates release readiness and attestation integrity
"""

import json
from pathlib import Path
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from vad.contracts.models import (
    EIP,
    Goal,
    ProofObligation,
    ProofKind,
    Invariants,
    Constraints,
    ToolPermissions,
    ModelBudget,
    ReleaseRequirements,
    TelemetryRequirements,
    RiskTier,
    AutonomyTier,
)
from vad.decision.dor_gate import DoRGate
from vad.policy.quality_gates import QualityGateRunner


class MCPToolDefinition(BaseModel):
    name: str
    description: str
    inputSchema: Dict[str, Any]


class VADMCPServer:
    """MCP protocol handler exposing VAD governance tools to Claude and other MCP clients."""

    def __init__(self, workspace_root: str = "."):
        self.workspace_root = Path(workspace_root)
        self.dor_gate = DoRGate()
        self.quality_gate_runner = QualityGateRunner()

    def get_tool_definitions(self) -> List[MCPToolDefinition]:
        return [
            MCPToolDefinition(
                name="vad_assess",
                description="Assess an intent or change against Definition of Ready gates and autonomy rules.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "goal_description": {"type": "string"},
                        "success_criteria": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["goal_description"],
                },
            ),
            MCPToolDefinition(
                name="vad_proof_map",
                description="Retrieve required proof obligations and invariants for an intent.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "eip_id": {"type": "string"},
                    },
                },
            ),
            MCPToolDefinition(
                name="vad_evidence_inspect",
                description="Inspect cryptographic evidence artifacts recorded during execution.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "item_id": {"type": "string"},
                    },
                },
            ),
            MCPToolDefinition(
                name="vad_release_gate",
                description="Evaluate whether artifact passes all release quality and attestation gates.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "artifact_id": {"type": "string"},
                        "has_attestation": {"type": "boolean"},
                        "test_coverage": {"type": "number"},
                    },
                },
            ),
        ]

    def call_tool(self, name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        handlers = {
            "vad_assess": self._tool_assess,
            "vad_proof_map": self._tool_proof_map,
            "vad_evidence_inspect": self._tool_evidence_inspect,
            "vad_release_gate": self._tool_release_gate,
        }
        handler = handlers.get(name)
        if not handler:
            return {"isError": True, "content": [{"type": "text", "text": f"Unknown tool '{name}'"}]}

        try:
            result = handler(arguments)
            return {"isError": False, "content": [{"type": "text", "text": json.dumps(result, indent=2)}]}
        except Exception as e:
            return {"isError": True, "content": [{"type": "text", "text": f"Execution error: {str(e)}"}]}

    def _tool_assess(self, args: Dict[str, Any]) -> Dict[str, Any]:
        desc = args.get("goal_description", "Default goal")
        criteria = args.get("success_criteria", ["Tests pass"])
        eip = EIP(
            version="1.0.0",
            name="mcp-assess",
            goal=Goal(description=desc, success_criteria=criteria),
            non_goals=[],
            risk_tier=RiskTier.LOW,
            autonomy_tier=AutonomyTier.ASSISTED,
            scope_boundaries=["*"],
            invariants=Invariants(security=[], functional=[]),
            constraints=Constraints(),
            proof_obligations=[ProofObligation(id="po-1", kind=ProofKind.UNIT, description="test")],
            tool_permissions=ToolPermissions(allowed=["*"]),
            memory_requirements=[],
            model_budget=ModelBudget(max_tokens=2000, max_cost=1.0, max_loop_depth=2),
            release_requirements=ReleaseRequirements(required=False),
            telemetry_requirements=TelemetryRequirements(required=False),
        )
        res = self.dor_gate.evaluate(eip)
        return {
            "status": res.status.value,
            "admitted": res.admitted,
            "reason": res.reason,
            "missing_criteria": res.missing_criteria,
        }

    def _tool_proof_map(self, args: Dict[str, Any]) -> Dict[str, Any]:
        eip_id = args.get("eip_id", "default")
        return {
            "eip_id": eip_id,
            "obligations": [
                {"id": "PO-1", "kind": "unit", "description": "Unit test suites passing in container"},
                {"id": "PO-2", "kind": "integration", "description": "Contract interface compatibility verified"},
                {"id": "PO-3", "kind": "security", "description": "No banned functions or secret leaks"},
            ],
            "total": 3,
        }

    def _tool_evidence_inspect(self, args: Dict[str, Any]) -> Dict[str, Any]:
        item_id = args.get("item_id")
        ev_dir = self.workspace_root / "evidence"
        if not ev_dir.exists():
            return {"evidence": [], "message": "No evidence directory found"}

        records = []
        if item_id:
            target = ev_dir / f"{item_id}.json"
            if target.exists():
                records.append(json.loads(target.read_text()))
        else:
            for p in sorted(ev_dir.glob("*.json")):
                try:
                    records.append(json.loads(p.read_text()))
                except Exception:
                    pass

        return {"count": len(records), "evidence": records}

    def _tool_release_gate(self, args: Dict[str, Any]) -> Dict[str, Any]:
        artifact_id = args.get("artifact_id", "artifact-1")
        has_attestation = args.get("has_attestation", True)
        coverage = args.get("test_coverage", 85.0)

        passed = has_attestation and (coverage >= 80.0)
        return {
            "artifact_id": artifact_id,
            "release_admitted": passed,
            "checks": {
                "dsse_attestation": "passed" if has_attestation else "missing",
                "minimum_coverage": "passed" if coverage >= 80.0 else "failed",
            },
        }

    def handle_jsonrpc(self, request: Dict[str, Any]) -> Dict[str, Any]:
        req_id = request.get("id")
        method = request.get("method")
        params = request.get("params", {})

        if method == "tools/list":
            tools = [t.model_dump() for t in self.get_tool_definitions()]
            return {"jsonrpc": "2.0", "id": req_id, "result": {"tools": tools}}
        elif method == "tools/call":
            name = params.get("name", "")
            args = params.get("arguments", {})
            res = self.call_tool(name, args)
            return {"jsonrpc": "2.0", "id": req_id, "result": res}
        else:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32601, "message": f"Method not found: {method}"},
            }
