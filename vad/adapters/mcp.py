import sys
import json
import os
import traceback
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
from vad.guards.execution import ExecutionGuard
from vad.repo.intake import assess_repository
from vad.repo.patch_apply import apply_unified_diff
from vad.repo.dependencies import assess_dependency_changes
from vad.repo.git import inspect_git_state
from vad.contracts.models import EIP
from vad.proof.plan import ProofPlan
from vad.verify.runner import VerifierRunner
from vad.signing.local import LocalDevelopmentSigner
from vad.signing.models import SignatureEnvelope
from vad.router.providers.fake import FakeProvider, ProviderCompletionRequest
from vad.router.providers.openai_sdk import OpenAIProvider
from vad.swarm.state import SwarmState
from vad.control_plane.mcp_gateway import (
    GatewayToolVisibilityRequest,
    McpToolCallRecorder,
    authorize_gateway_tool_call,
    filter_gateway_tools,
    mcp_tool_definitions,
)

class MCPAdapter:
    def __init__(self):
        self.connected = False
        self.tools: dict[str, Any] = {}

    def connect(self):
        self.tools = {
            "validate_eip": handle_validate_eip,
            "run_proofs": handle_run_proofs,
            "retro_analyze": handle_retro_analyze,
            "query_retrospective": handle_query_retrospective,
            "submit_retrospective": handle_submit_retrospective,
            "repo_assess": handle_repo_assess,
            "repo_patch": handle_repo_patch,
            "repo_run": handle_repo_run,
            "sign_verify": handle_sign_verify,
            "evidence_inspect": handle_evidence_inspect,
            "provider_inventory": handle_provider_inventory,
            "provider_test": handle_provider_test,
            "swarm_status": handle_swarm_status,
        }
        self.connected = True
        return self

    def call_tool(self, tool_name: str, params: Dict[str, Any]) -> Any:
        if not self.connected:
            self.connect()
        recorder = _recorder_from_args(params)
        if recorder is not None:
            recorder.record_started(tool_name=tool_name, args=params)
        authorization = authorize_gateway_tool_call(tool_name, _visibility_request_from_args(params))
        if not authorization.visible:
            result = _with_mcp_attribution(
                _policy_denied_result(tool_name, authorization.reason),
                tool_name,
                params,
            )
            if recorder is not None:
                recorder.record_denied(tool_name=tool_name, reason=authorization.reason)
                recorder.record_finished(tool_name=tool_name, result=result)
            return result
        handler = self.tools.get(tool_name)
        if handler is None:
            if recorder is not None:
                recorder.record_denied(tool_name=tool_name, reason="unknown tool")
            result = _with_mcp_attribution(
                {"content": [{"type": "text", "text": f"Unknown tool: {tool_name}"}], "isError": True},
                tool_name,
                params,
            )
            return result
        result = _with_mcp_attribution(handler(params), tool_name, params)
        if recorder is not None:
            if result.get("isError", False):
                recorder.record_denied(tool_name=tool_name, reason="tool returned error")
            recorder.record_finished(tool_name=tool_name, result=result)
        return result


def _memory_store() -> LocalMemoryStore:
    return LocalMemoryStore(os.environ.get("VAD_RETRO_MEMORY_FILE"))


def _memory_gateway() -> MemoryGateway:
    return MemoryGateway(store=_memory_store(), redactor=Redactor())

def _json_result(payload: Any) -> Dict[str, Any]:
    return {"content": [{"type": "text", "text": json.dumps(payload, indent=2, sort_keys=True)}]}

def _error_result(message: str, payload: Any | None = None) -> Dict[str, Any]:
    text = message if payload is None else f"{message}\n{json.dumps(payload, indent=2, sort_keys=True)}"
    return {"content": [{"type": "text", "text": text}], "isError": True}

def _policy_denied_result(tool_name: str, reason: str) -> Dict[str, Any]:
    return _error_result(f"MCP tool policy denied: {tool_name}", {"reason": reason})

def _with_mcp_attribution(result: Dict[str, Any], tool_name: str, args: Dict[str, Any]) -> Dict[str, Any]:
    attribution = {
        "event": "mcp_tool_call",
        "tool": tool_name,
        "client_id": args.get("client_id"),
        "actor_id": args.get("actor_id"),
        "run_id": args.get("run_id"),
        "evidence_digest": args.get("evidence_digest"),
        "policy_decision": {
            "allow": not result.get("isError", False),
            "reasons": ["MCP tool call completed"] if not result.get("isError", False) else [],
            "denials": [] if not result.get("isError", False) else [f"MCP tool call denied or failed: {tool_name}"],
            "requires_human": False,
        },
    }
    if attribution["evidence_digest"] is None:
        attribution["evidence_digest"] = _extract_evidence_digest(result)
    result["mcp_attribution"] = attribution
    return result

def _recorder_from_args(args: Dict[str, Any]) -> McpToolCallRecorder | None:
    db_path = args.get("control_plane_db")
    client_id = args.get("client_id")
    actor_id = args.get("actor_id") or args.get("actor") or client_id
    role = args.get("role") or args.get("client_role") or "observer"
    if not db_path or not client_id or not actor_id:
        return None
    return McpToolCallRecorder(
        db_path=Path(db_path),
        client_id=client_id,
        actor_id=actor_id,
        role=role,
        run_id=args.get("run_id"),
        task_id=args.get("task_id"),
    )

def _visibility_request_from_args(args: Dict[str, Any]) -> GatewayToolVisibilityRequest:
    return GatewayToolVisibilityRequest(
        client_id=args.get("client_id"),
        run_id=args.get("run_id"),
        role=args.get("role") or args.get("client_role") or "observer",
        approved_high_risk_tools=tuple(args.get("approved_high_risk_tools", ()) or ()),
    )

def _extract_evidence_digest(result: Dict[str, Any]) -> str | None:
    try:
        text = result["content"][0]["text"]
        payload = json.loads(text)
        return payload.get("evidence_hash") or payload.get("payload_digest")
    except Exception:
        return None

def _load_structured_file(file_path: Path):
    with open(file_path, "r") as f:
        if file_path.suffix in [".yml", ".yaml"]:
            return yaml.safe_load(f)
        return json.load(f)

# Define tools exposed via MCP from the control-plane gateway registry.
TOOLS = mcp_tool_definitions()


def list_tools_for_client(params: Dict[str, Any] | None = None) -> Dict[str, Any]:
    params = params or {}
    request = _visibility_request_from_args(params)
    result = filter_gateway_tools(request)
    return {
        "tools": result.to_mcp_tools(),
        "tool_visibility_audit": [record.model_dump(mode="json") for record in result.audit],
    }

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


def mcp_response(response_id: Any, result: Any = None, error: Any = None) -> dict[str, Any]:
    response = {"jsonrpc": "2.0", "id": response_id}
    if error is not None:
        response["error"] = error
    else:
        response["result"] = result
    return response


def handle_json_rpc_request(request: Dict[str, Any]) -> dict[str, Any] | None:
    if request.get("jsonrpc") != "2.0":
        return None

    req_id = request.get("id")
    method = request.get("method")
    params = request.get("params", {})

    if method == "initialize":
        return mcp_response(req_id, result={
            "protocolVersion": params.get("protocolVersion", "2024-11-05"),
            "capabilities": {
                "tools": {}
            },
            "serverInfo": {
                "name": "vad-mcp",
                "version": "0.1.0"
            }
        })

    if method == "notifications/initialized":
        return None

    if method == "tools/list":
        return mcp_response(req_id, result=list_tools_for_client(params))

    if method == "tools/call":
        tool_name = params.get("name")
        tool_args = params.get("arguments", {})
        return mcp_response(req_id, result=handle_tool_call(tool_name, tool_args))

    if method == "ping":
        return mcp_response(req_id, result={})

    if req_id is not None:
        return mcp_response(req_id, error={"code": -32601, "message": f"Method not found: {method}"})
    return None


def handle_tool_call(tool_name: str, tool_args: Dict[str, Any]) -> Dict[str, Any]:
    handler = TOOL_HANDLERS.get(tool_name)
    recorder = _recorder_from_args(tool_args)
    if recorder is not None:
        recorder.record_started(tool_name=tool_name, args=tool_args)
    authorization = authorize_gateway_tool_call(tool_name, _visibility_request_from_args(tool_args))
    if not authorization.visible:
        result = _with_mcp_attribution(
            _policy_denied_result(tool_name, authorization.reason),
            tool_name,
            tool_args,
        )
        if recorder is not None:
            recorder.record_denied(tool_name=tool_name, reason=authorization.reason)
            recorder.record_finished(tool_name=tool_name, result=result)
        return result
    if handler is None:
        if recorder is not None:
            recorder.record_denied(tool_name=tool_name, reason="unknown tool")
        return _with_mcp_attribution(
            {"content": [{"type": "text", "text": f"Unknown tool: {tool_name}"}], "isError": True},
            tool_name,
            tool_args,
        )
    result = _with_mcp_attribution(handler(tool_args), tool_name, tool_args)
    if recorder is not None:
        if result.get("isError", False):
            recorder.record_denied(tool_name=tool_name, reason="tool returned error")
        recorder.record_finished(tool_name=tool_name, result=result)
    return result

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
        res = ExecutionGuard().run(cmd)
        out = f"STDOUT:\n{res.stdout}\nSTDERR:\n{res.stderr}"
        if res.allowed and res.exit_code == 0:
            return {"content": [{"type": "text", "text": f"Tests passed successfully!\n\n{out}"}]}
        else:
            return {"content": [{"type": "text", "text": f"Tests failed or were denied with return code {res.exit_code}.\n\n{out}"}], "isError": True}
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
        gateway = _memory_gateway()
        analyzer = RetroAnalyzer(gateway)
        res = analyzer.analyze(bundle)
        return {"content": [{"type": "text", "text": f"Retro analysis completed. Learning generated:\n{json.dumps(res, indent=2)}"}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Retro analysis failed:\n{traceback.format_exc()}"}], "isError": True}

def handle_query_retrospective(args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        gateway = _memory_gateway()
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
        gateway = _memory_gateway()
        entry = MemoryEntry(
            id=str(uuid.uuid4()),
            scope=MemoryScope.RETROSPECTIVE,
            content=json.dumps({"failures": [], "learning": learning})
        )
        gateway.store_memory(entry)
        return {"content": [{"type": "text", "text": f"Submitted new retrospective entry successfully. ID: {entry.id}"}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Failed to submit retrospective memory:\n{traceback.format_exc()}"}], "isError": True}

def handle_repo_assess(args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        return _json_result(assess_repository(Path(args["path"])).model_dump(mode="json"))
    except Exception as e:
        return _error_result(f"Repository assessment failed: {e}")

def handle_repo_patch(args: Dict[str, Any]) -> Dict[str, Any]:
    return _repo_patch_like(args)

def handle_repo_run(args: Dict[str, Any]) -> Dict[str, Any]:
    return _repo_patch_like(args)

def _repo_patch_like(args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        repo_path = Path(args["path"])
        git_state = inspect_git_state(repo_path, allow_dirty=bool(args.get("allow_dirty", False)))
        if not git_state.autonomous_patch_allowed:
            return _error_result("Repository patch blocked", {"blocker": git_state.blocker})
        eip = EIP(**_load_structured_file(Path(args["eip_file"])))
        plan = ProofPlan(**_load_structured_file(Path(args["proof_plan_file"])))
        apply_result = apply_unified_diff(repo_path, Path(args["patch_file"]).read_text(encoding="utf-8"))
        if not apply_result.applied:
            return _error_result("Repository patch failed", {"blocker": apply_result.blocker})
        dependency_decision = assess_dependency_changes(
            apply_result.changed_files,
            approved=bool(args.get("approve_dependencies", False)),
        ).decision()
        rollback = None
        if not dependency_decision.allow:
            rollback = apply_result.journal.rollback() if apply_result.journal else None
            return _error_result("Repository patch denied", _repo_patch_payload(apply_result, dependency_decision, None, rollback))
        verification = VerifierRunner(eip, plan, cwd=str(repo_path.resolve())).run()
        if not verification.passed:
            rollback = apply_result.journal.rollback() if apply_result.journal else None
            return _error_result("Repository proofs failed", _repo_patch_payload(apply_result, dependency_decision, verification, rollback))
        return _json_result(_repo_patch_payload(apply_result, dependency_decision, verification, rollback))
    except Exception as e:
        return _error_result(f"Repository automation failed: {e}")

def _repo_patch_payload(apply_result, dependency_decision, verification, rollback):
    return {
        "applied": apply_result.applied,
        "changed_files": apply_result.changed_files,
        "journal": apply_result.journal.to_evidence(rollback).model_dump(mode="json") if apply_result.journal else None,
        "dependency_decision": dependency_decision.model_dump(mode="json"),
        "verification": verification.model_dump(mode="json") if verification else None,
        "rolled_back": rollback.rolled_back if rollback else False,
    }

def handle_sign_verify(args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        signed = _load_structured_file(Path(args["signed_file"]))
        envelope = SignatureEnvelope(**signed["signature"])
        verified = LocalDevelopmentSigner(envelope.key_id, Path(args["secret_file"]).read_bytes()).verify_payload(signed["payload"], envelope)
        payload = {"verified": verified, "key_id": envelope.key_id, "payload_digest": envelope.payload_digest}
        return _json_result(payload) if verified else _error_result("Signature verification failed", payload)
    except Exception as e:
        return _error_result(f"Signature verification failed: {e}")

def handle_evidence_inspect(args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        payload = _load_structured_file(Path(args["file"]))
        return _json_result({"evidence_hash": EvidenceBundle(payload).compute_hash(), "keys": sorted(payload.keys()) if isinstance(payload, dict) else []})
    except Exception as e:
        return _error_result(f"Evidence inspect failed: {e}")

def handle_provider_inventory(args: Dict[str, Any]) -> Dict[str, Any]:
    provider_name = args["provider"]
    if provider_name == "fake":
        return _json_result(FakeProvider().inventory().model_dump(mode="json"))
    if provider_name == "openai":
        return _json_result(OpenAIProvider().inventory().model_dump(mode="json"))
    return _error_result(f"Unsupported provider: {provider_name}")

def handle_provider_test(args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        request = ProviderCompletionRequest(prompt=args["prompt"], model=args.get("model", "fake-chat"))
        result = FakeProvider().complete(request)
        return _json_result(result.model_dump(mode="json")) if result.allowed else _error_result("Provider test failed", result.model_dump(mode="json"))
    except Exception as e:
        return _error_result(f"Provider test failed: {e}")

def handle_swarm_status(args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        state = SwarmState.load(args["state"])
        return _json_result({
            "run_id": state.run_id,
            "tasks": [task.model_dump(mode="json") for task in state.graph.tasks],
            "messages": [message.model_dump(mode="json") for message in state.messages],
            "final_decision": "passed" if all(task.status.value == "completed" for task in state.graph.tasks) else "blocked",
        })
    except Exception as e:
        return _error_result(f"Swarm status failed: {e}")

TOOL_HANDLERS = {
    "validate_eip": handle_validate_eip,
    "run_proofs": handle_run_proofs,
    "retro_analyze": handle_retro_analyze,
    "query_retrospective": handle_query_retrospective,
    "submit_retrospective": handle_submit_retrospective,
    "repo_assess": handle_repo_assess,
    "repo_patch": handle_repo_patch,
    "repo_run": handle_repo_run,
    "sign_verify": handle_sign_verify,
    "evidence_inspect": handle_evidence_inspect,
    "provider_inventory": handle_provider_inventory,
    "provider_test": handle_provider_test,
    "swarm_status": handle_swarm_status,
}

def serve():
    sys.stderr.write("VAD MCP server starting...\n")
    sys.stderr.flush()

    while True:
        try:
            line = sys.stdin.readline()
            if not line:
                break
            request = json.loads(line.strip())
            response = handle_json_rpc_request(request)
            if response is not None:
                send_response(response.get("id"), result=response.get("result"), error=response.get("error"))
                    
        except KeyboardInterrupt:
            break
        except Exception as e:
            sys.stderr.write(f"Error: {e}\n{traceback.format_exc()}")
            sys.stderr.flush()

if __name__ == "__main__":
    serve()
