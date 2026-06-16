import sys
import json
import traceback
import subprocess
from pathlib import Path
from typing import Any, Dict
import yaml

# Import VAD business logic
from vad.contracts.models import EIP
from vad.memory.contracts import MemoryEntry, MemoryScope
from vad.memory.gateway import MemoryGateway
from vad.memory.stores.local import LocalMemoryStore
from vad.memory.redaction import Redactor
from vad.feedback.retro import RetroAnalyzer
from vad.evidence.bundle import EvidenceBundle

class MCPAdapter:
    def connect(self):
        pass

    def call_tool(self, tool_name: str, params: Dict[str, Any]) -> Any:
        pass

# Define tools exposed via MCP
TOOLS = [
    {
        "name": "validate_eip",
        "description": "Validates a VAD EIP (Intent Document) against the schema and validation constraints.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "file": {
                    "type": "string",
                    "description": "Path to the EIP file (YAML or JSON)"
                }
            },
            "required": ["file"]
        }
    },
    {
        "name": "run_proofs",
        "description": "Executes the defined proof obligations (e.g. running pytest) to verify requirements.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "target": {
                    "type": "string",
                    "description": "Optional pytest target (e.g. tests/test_models.py)"
                }
            }
        }
    },
    {
        "name": "retro_analyze",
        "description": "Runs retrospective analysis on an evidence bundle, generating learnings and persisting them to MemoryScope.RETROSPECTIVE.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "file": {
                    "type": "string",
                    "description": "Path to the evidence bundle JSON or YAML file"
                }
            },
            "required": ["file"]
        }
    },
    {
        "name": "query_retrospective",
        "description": "Queries historical retrospective learnings from the VAD memory store.",
        "inputSchema": {
            "type": "object",
            "properties": {}
        }
    },
    {
        "name": "submit_retrospective",
        "description": "Manually records a learning back into the retrospective memory scope.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "learning": {
                    "type": "string",
                    "description": "A clear, actionable learning or rule to record"
                }
            },
            "required": ["learning"]
        }
    }
]

def send_response(response_id: Any, result: Any = None, error: Any = None):
    resp = {"jsonrpc": "2.0", "id": response_id}
    if error is not None:
        resp["error"] = error
    else:
        resp["result"] = result
    sys.stdout.write(json.dumps(resp) + "\n")
    sys.stdout.flush()

def send_error(response_id: Any, code: int, message: str, data: Any = None):
    err = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    send_response(response_id, error=err)

def handle_validate_eip(args: Dict[str, Any]) -> Dict[str, Any]:
    file_path = Path(args["file"])
    if not file_path.exists():
        return {"content": [{"type": "text", "text": f"Error: EIP file not found at {file_path.absolute()}"}], "isError": True}
    try:
        with open(file_path, "r") as f:
            if file_path.suffix in [".yml", ".yaml"]:
                data = yaml.safe_load(f)
            else:
                data = json.load(f)
        EIP(**data)
        return {"content": [{"type": "text", "text": "Validation successful: EIP is valid and conforms to schemas/eip.schema.json."}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Validation failed:\n{traceback.format_exc()}"}], "isError": True}

def handle_run_proofs(args: Dict[str, Any]) -> Dict[str, Any]:
    target = args.get("target")
    cmd = ["pytest"]
    if target:
        cmd.append(target)
    try:
        res = subprocess.run(cmd, capture_output=True, text=True)
        out = f"STDOUT:\n{res.stdout}\nSTDERR:\n{res.stderr}"
        if res.returncode == 0:
            return {"content": [{"type": "text", "text": f"Tests passed successfully!\n\n{out}"}]}
        else:
            return {"content": [{"type": "text", "text": f"Tests failed with return code {res.returncode}.\n\n{out}"}], "isError": True}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Failed to execute tests: {e}\n{traceback.format_exc()}"}], "isError": True}

def handle_retro_analyze(args: Dict[str, Any]) -> Dict[str, Any]:
    file_path = Path(args["file"])
    if not file_path.exists():
        return {"content": [{"type": "text", "text": f"Error: Evidence bundle not found at {file_path.absolute()}"}], "isError": True}
    try:
        with open(file_path, "r") as f:
            if file_path.suffix in [".yml", ".yaml"]:
                data = yaml.safe_load(f)
            else:
                data = json.load(f)
        bundle = EvidenceBundle(data)
        gateway = MemoryGateway(store=LocalMemoryStore(), redactor=Redactor())
        analyzer = RetroAnalyzer(gateway)
        res = analyzer.analyze(bundle)
        return {"content": [{"type": "text", "text": f"Retro analysis completed. Learning generated:\n{json.dumps(res, indent=2)}"}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Retro analysis failed:\n{traceback.format_exc()}"}], "isError": True}

def handle_query_retrospective(args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        gateway = MemoryGateway(store=LocalMemoryStore(), redactor=Redactor())
        entries = gateway.get_by_scope(MemoryScope.RETROSPECTIVE)
        if not entries:
            return {"content": [{"type": "text", "text": "No retrospective learnings found in the memory store."}]}
        formatted = []
        for entry in entries:
            formatted.append(f"Entry ID: {entry.id}\nContent: {entry.content}\nMetadata: {entry.metadata}\n---")
        return {"content": [{"type": "text", "text": "\n".join(formatted)}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Failed to query retrospective memory:\n{traceback.format_exc()}"}], "isError": True}

def handle_submit_retrospective(args: Dict[str, Any]) -> Dict[str, Any]:
    learning = args["learning"]
    try:
        import uuid
        gateway = MemoryGateway(store=LocalMemoryStore(), redactor=Redactor())
        entry = MemoryEntry(
            id=str(uuid.uuid4()),
            scope=MemoryScope.RETROSPECTIVE,
            content=json.dumps({"failures": [], "learning": learning})
        )
        gateway.store_memory(entry)
        return {"content": [{"type": "text", "text": f"Submitted new retrospective entry successfully. ID: {entry.id}"}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Failed to submit retrospective memory:\n{traceback.format_exc()}"}], "isError": True}

def serve():
    sys.stderr.write("VAD MCP server starting...\n")
    sys.stderr.flush()

    while True:
        try:
            line = sys.stdin.readline()
            if not line:
                break
            request = json.loads(line.strip())
            
            if request.get("jsonrpc") != "2.0":
                continue
                
            req_id = request.get("id")
            method = request.get("method")
            params = request.get("params", {})
            
            if method == "initialize":
                result = {
                    "protocolVersion": params.get("protocolVersion", "2024-11-05"),
                    "capabilities": {
                        "tools": {}
                    },
                    "serverInfo": {
                        "name": "vad-mcp",
                        "version": "0.1.0"
                    }
                }
                send_response(req_id, result=result)
                
            elif method == "notifications/initialized":
                pass
                
            elif method == "tools/list":
                send_response(req_id, result={"tools": TOOLS})
                
            elif method == "tools/call":
                tool_name = params.get("name")
                tool_args = params.get("arguments", {})
                
                if tool_name == "validate_eip":
                    res = handle_validate_eip(tool_args)
                elif tool_name == "run_proofs":
                    res = handle_run_proofs(tool_args)
                elif tool_name == "retro_analyze":
                    res = handle_retro_analyze(tool_args)
                elif tool_name == "query_retrospective":
                    res = handle_query_retrospective(tool_args)
                elif tool_name == "submit_retrospective":
                    res = handle_submit_retrospective(tool_args)
                else:
                    send_error(req_id, -32601, f"Method not found: {tool_name}")
                    continue
                
                send_response(req_id, result=res)
                
            elif method == "ping":
                send_response(req_id, result={})
                
            else:
                if req_id is not None:
                    send_error(req_id, -32601, f"Method not found: {method}")
                    
        except KeyboardInterrupt:
            break
        except Exception as e:
            sys.stderr.write(f"Error: {e}\n{traceback.format_exc()}")
            sys.stderr.flush()

if __name__ == "__main__":
    serve()
