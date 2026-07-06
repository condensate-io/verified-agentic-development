import io
import json
import sys
import pytest
from unittest.mock import patch, MagicMock
from vad.adapters.mcp import MCPAdapter, serve
from vad.control_plane.events import ControlPlaneEventKind, ControlPlaneEventStatus
from vad.signing.local import LocalDevelopmentSigner
from vad.server.db.store import ServerStore
from vad.swarm.agents import AgentRole
from vad.swarm.state import SwarmState
from vad.swarm.tasks import SwarmTask, SwarmTaskGraph, SwarmTaskStatus

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

def test_mcp_adapter_connect_initializes_state():
    adapter = MCPAdapter()

    returned = adapter.connect()

    assert returned is adapter
    assert adapter.connected is True
    assert "validate_eip" in adapter.tools
    assert "retro_analyze" in adapter.tools
    assert "provider_inventory" in adapter.tools
    assert "swarm_status" in adapter.tools

def test_mcp_adapter_repeated_connect_is_safe():
    adapter = MCPAdapter().connect()
    first_tools = adapter.tools

    adapter.connect()

    assert adapter.connected is True
    assert adapter.tools.keys() == first_tools.keys()

def test_mcp_adapter_call_tool_validate_eip(tmp_path):
    eip_file = tmp_path / "eip.yaml"
    eip_file.write_text("""
version: 1.0.0
name: adapter-test
risk_tier: low
autonomy_tier: assisted
goal:
  description: Test
  success_criteria: ["Pass"]
non_goals: []
scope_boundaries: ["tests"]
invariants:
  functional: ["works"]
constraints: {}
proof_obligations:
  - id: po-1
    kind: unit
    description: unit proof
tool_permissions:
  allowed: ["pytest"]
  denied: ["network"]
memory_requirements: []
model_budget:
  max_tokens: 1000
  max_cost: 1.0
  max_loop_depth: 3
release_requirements:
  required: false
  gates: []
telemetry_requirements:
  required: false
  signals: []
""")

    result = MCPAdapter().call_tool("validate_eip", {"file": str(eip_file)})

    assert "Validation successful" in result["content"][0]["text"]

def test_mcp_adapter_call_tool_retro_analyze(tmp_path):
    bundle_file = tmp_path / "bundle.json"
    bundle_file.write_text(json.dumps({"failures": ["policy denied shell"]}))

    result = MCPAdapter().call_tool("retro_analyze", {"file": str(bundle_file), "role": "auditor"})

    assert "Retro analysis completed" in result["content"][0]["text"]
    assert "restrict_tool" in result["content"][0]["text"]

def test_mcp_adapter_call_tool_unknown_returns_controlled_error():
    result = MCPAdapter().call_tool("missing_tool", {})

    assert result["isError"] is True
    assert "policy denied" in result["content"][0]["text"]
    assert result["mcp_attribution"]["policy_decision"]["allow"] is False

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
    assert "query_retrospective" in tool_names
    assert "repo_assess" in tool_names
    assert "evidence_inspect" in tool_names
    assert "provider_inventory" in tool_names
    assert "swarm_status" in tool_names
    assert "repo_patch" not in tool_names
    assert "tool_visibility_audit" in resp["result"]
    assert any(record["tool_name"] == "repo_patch" and not record["visible"] for record in resp["result"]["tool_visibility_audit"])

def test_mcp_validate_eip(tmp_path):
    eip_file = tmp_path / "eip.yaml"
    eip_data = {
        "version": "1.0.0",
        "name": "test-eip",
        "risk_tier": "low",
        "autonomy_tier": "assisted",
        "goal": {
            "description": "Test EIP goal",
            "success_criteria": ["All tests pass"]
        },
        "non_goals": [],
        "scope_boundaries": ["tests"],
        "invariants": {
            "functional": ["System remains operational"]
        },
        "constraints": {},
        "proof_obligations": [
            {
                "id": "PO-1",
                "kind": "unit",
                "description": "Verify system is operational"
            }
        ],
        "tool_permissions": {
            "allowed": ["pytest"],
            "denied": ["network"]
        },
        "memory_requirements": [
            {
                "scope": "project",
                "purpose": "mcp validation"
            }
        ],
        "model_budget": {
            "max_tokens": 1000,
            "max_cost": 1.0,
            "max_loop_depth": 3
        },
        "release_requirements": {
            "required": False,
            "gates": []
        },
        "telemetry_requirements": {
            "required": False,
            "signals": []
        }
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

def test_mcp_run_proofs_denies_forbidden_target():
    req = {
        "jsonrpc": "2.0",
        "id": 4,
        "method": "tools/call",
        "params": {
            "name": "run_proofs",
            "arguments": {"target": "tests && echo bad", "role": "verifier"}
        }
    }

    stdin_mock = io.StringIO(json.dumps(req) + "\n")
    stdout_mock = io.StringIO()

    with patch('sys.stdin', stdin_mock), patch('sys.stdout', stdout_mock), patch('sys.stderr', MagicMock()):
        serve()

    stdout_mock.seek(0)
    response_line = stdout_mock.readline()
    resp = json.loads(response_line)
    assert resp["id"] == 4
    assert resp["result"]["isError"] is True
    assert "shell control" in resp["result"]["content"][0]["text"]

def test_mcp_retrospective_memory_persists_across_calls(tmp_path):
    memory_file = tmp_path / "retro-memory.json"
    submit_req = {
        "jsonrpc": "2.0",
        "id": 5,
        "method": "tools/call",
        "params": {
            "name": "submit_retrospective",
            "arguments": {"learning": "Contact me at test@example.com before changing auth.", "role": "auditor"}
        }
    }
    query_req = {
        "jsonrpc": "2.0",
        "id": 6,
        "method": "tools/call",
        "params": {
            "name": "query_retrospective",
            "arguments": {}
        }
    }

    with patch.dict("os.environ", {"VAD_RETRO_MEMORY_FILE": str(memory_file)}):
        submit_out = io.StringIO()
        with patch('sys.stdin', io.StringIO(json.dumps(submit_req) + "\n")), patch('sys.stdout', submit_out), patch('sys.stderr', MagicMock()):
            serve()
        query_out = io.StringIO()
        with patch('sys.stdin', io.StringIO(json.dumps(query_req) + "\n")), patch('sys.stdout', query_out), patch('sys.stderr', MagicMock()):
            serve()

    response = json.loads(query_out.getvalue().splitlines()[0])
    text = response["result"]["content"][0]["text"]
    assert "test@example.com" not in text
    assert "[REDACTED]" in text
    assert "changing auth" in text


def test_mcp_provider_inventory_returns_fake_provider():
    result = MCPAdapter().call_tool("provider_inventory", {"provider": "fake"})

    payload = json.loads(result["content"][0]["text"])
    assert payload["provider_name"] == "fake"
    assert payload["models"][0]["name"] == "fake-chat"


def test_mcp_provider_test_records_request_id():
    result = MCPAdapter().call_tool("provider_test", {"prompt": "hello", "role": "verifier"})

    payload = json.loads(result["content"][0]["text"])
    assert payload["allowed"] is True
    assert payload["provider_request_id"] == "fake-1"


def test_mcp_sign_verify_accepts_valid_signed_artifact(tmp_path):
    secret_file = tmp_path / "secret.key"
    signed_file = tmp_path / "signed.json"
    secret_file.write_bytes(b"secret")
    payload = {"run_id": "run-1"}
    envelope = LocalDevelopmentSigner("local-dev", b"secret").sign_payload(payload)
    signed_file.write_text(json.dumps({"payload": payload, "signature": envelope.model_dump(mode="json")}), encoding="utf-8")

    result = MCPAdapter().call_tool("sign_verify", {
        "signed_file": str(signed_file),
        "secret_file": str(secret_file),
        "role": "release_guardian",
        "approved_high_risk_tools": ["sign_verify"],
    })

    payload = json.loads(result["content"][0]["text"])
    assert payload["verified"] is True


def test_mcp_evidence_inspect_returns_hash(tmp_path):
    evidence_file = tmp_path / "evidence.json"
    evidence_file.write_text(json.dumps({"run_id": "run-1"}), encoding="utf-8")

    result = MCPAdapter().call_tool("evidence_inspect", {"file": str(evidence_file)})

    payload = json.loads(result["content"][0]["text"])
    assert len(payload["evidence_hash"]) == 64
    assert payload["keys"] == ["run_id"]
    assert result["mcp_attribution"]["evidence_digest"] == payload["evidence_hash"]


def test_mcp_swarm_status_reads_persisted_state(tmp_path):
    state_file = tmp_path / "swarm.json"
    state = SwarmState(
        run_id="run-1",
        graph=SwarmTaskGraph(tasks=[
            SwarmTask(task_id="plan", role=AgentRole.PLANNER, description="plan", status=SwarmTaskStatus.COMPLETED),
        ]),
    )
    state.save(state_file)

    result = MCPAdapter().call_tool("swarm_status", {"state": str(state_file)})

    payload = json.loads(result["content"][0]["text"])
    assert payload["run_id"] == "run-1"
    assert payload["final_decision"] == "passed"


def test_mcp_repo_assess_emits_repository_json(tmp_path):
    repo = tmp_path / "repo"
    git_dir = repo / ".git"
    (git_dir / "refs" / "heads").mkdir(parents=True)
    (git_dir / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (git_dir / "refs" / "heads" / "main").write_text("abc123\n", encoding="utf-8")

    class FakeCompletedProcess:
        def __init__(self, stdout="", returncode=0):
            self.stdout = stdout
            self.returncode = returncode

    def runner(command, **kwargs):
        if command[:3] == ["git", "rev-parse", "HEAD"]:
            return FakeCompletedProcess("abc123\n")
        if command[:3] == ["git", "status", "--porcelain"]:
            return FakeCompletedProcess("")
        return FakeCompletedProcess(returncode=1)

    with patch("vad.repo.intake.subprocess.run", runner):
        result = MCPAdapter().call_tool("repo_assess", {"path": str(repo)})

    payload = json.loads(result["content"][0]["text"])
    assert payload["vcs_type"] == "git"
    assert payload["dirty_state"] == "clean"


def test_mcp_tool_call_records_client_actor_and_run_identity(tmp_path):
    evidence_file = tmp_path / "evidence.json"
    evidence_file.write_text(json.dumps({"run_id": "run-1"}), encoding="utf-8")

    result = MCPAdapter().call_tool("evidence_inspect", {
        "file": str(evidence_file),
        "client_id": "vscode",
        "actor_id": "builder-1",
        "run_id": "run-1",
    })

    attribution = result["mcp_attribution"]
    assert attribution["client_id"] == "vscode"
    assert attribution["actor_id"] == "builder-1"
    assert attribution["run_id"] == "run-1"
    assert attribution["policy_decision"]["allow"] is True


def test_mcp_denied_call_records_policy_decision():
    result = MCPAdapter().call_tool("provider_inventory", {"provider": "unknown", "client_id": "opencode"})

    attribution = result["mcp_attribution"]
    assert result["isError"] is True
    assert attribution["client_id"] == "opencode"
    assert attribution["policy_decision"]["allow"] is False
    assert "provider_inventory" in attribution["policy_decision"]["denials"][0]


def test_mcp_adapter_emits_started_and_finished_tool_call_events(tmp_path):
    evidence_file = tmp_path / "evidence.json"
    db_path = tmp_path / "control-plane.sqlite3"
    evidence_file.write_text(json.dumps({"run_id": "run-1", "status": "passed"}), encoding="utf-8")

    result = MCPAdapter().call_tool("evidence_inspect", {
        "file": str(evidence_file),
        "control_plane_db": str(db_path),
        "client_id": "codex-local",
        "actor_id": "builder-1",
        "role": "observer",
        "run_id": "run-1",
        "task_id": "inspect-evidence",
        "secret_file": "do-not-log",
    })

    digest = result["mcp_attribution"]["evidence_digest"]
    events = ServerStore(db_path).list_control_plane_events()
    assert [event.kind for event in events] == [
        ControlPlaneEventKind.TOOL_CALL_STARTED,
        ControlPlaneEventKind.TOOL_CALL_FINISHED,
    ]
    assert [event.status for event in events] == [
        ControlPlaneEventStatus.ACTIVE,
        ControlPlaneEventStatus.PASSED,
    ]
    assert events[0].client_id == "codex-local"
    assert events[0].actor == "builder-1"
    assert events[0].run_id == "run-1"
    assert events[0].task_id == "inspect-evidence"
    assert "[REDACTED]" in events[0].summary
    assert "do-not-log" not in events[0].summary
    assert events[1].evidence_digest == digest
    assert digest is not None


def test_mcp_adapter_emits_denied_and_failed_tool_call_events(tmp_path):
    db_path = tmp_path / "control-plane.sqlite3"

    result = MCPAdapter().call_tool("provider_inventory", {
        "provider": "unknown",
        "control_plane_db": str(db_path),
        "client_id": "opencode",
        "actor_id": "auditor-1",
        "role": "observer",
        "run_id": "run-2",
    })

    assert result["isError"] is True
    events = ServerStore(db_path).list_control_plane_events()
    assert [event.status for event in events] == [
        ControlPlaneEventStatus.ACTIVE,
        ControlPlaneEventStatus.BLOCKED,
        ControlPlaneEventStatus.FAILED,
    ]
    assert events[1].kind == ControlPlaneEventKind.TOOL_CALL_FINISHED
    assert "denied" in events[1].summary
    assert "tool returned error" in events[1].summary
    assert events[2].summary.startswith("MCP tool provider_inventory failed")


def test_mcp_stdio_tool_call_emits_control_plane_events(tmp_path):
    evidence_file = tmp_path / "evidence.json"
    db_path = tmp_path / "control-plane.sqlite3"
    evidence_file.write_text(json.dumps({"run_id": "run-stdio"}), encoding="utf-8")
    req = {
        "jsonrpc": "2.0",
        "id": 7,
        "method": "tools/call",
        "params": {
            "name": "evidence_inspect",
            "arguments": {
                "file": str(evidence_file),
                "control_plane_db": str(db_path),
                "client_id": "generic-mcp",
                "actor_id": "observer-1",
                "role": "observer",
                "run_id": "run-stdio",
            },
        },
    }

    stdout_mock = io.StringIO()
    with patch('sys.stdin', io.StringIO(json.dumps(req) + "\n")), patch('sys.stdout', stdout_mock), patch('sys.stderr', MagicMock()):
        serve()

    response = json.loads(stdout_mock.getvalue().splitlines()[0])
    events = ServerStore(db_path).list_control_plane_events()
    assert response["id"] == 7
    assert response["result"]["mcp_attribution"]["client_id"] == "generic-mcp"
    assert [event.status for event in events] == [
        ControlPlaneEventStatus.ACTIVE,
        ControlPlaneEventStatus.PASSED,
    ]


def _stdio_request(request):
    stdout_mock = io.StringIO()
    with patch('sys.stdin', io.StringIO(json.dumps(request) + "\n")), patch('sys.stdout', stdout_mock), patch('sys.stderr', MagicMock()):
        serve()
    return json.loads(stdout_mock.getvalue().splitlines()[0])


def _stdio_tools_for(params):
    response = _stdio_request({
        "jsonrpc": "2.0",
        "id": 20,
        "method": "tools/list",
        "params": params,
    })
    assert response["id"] == 20
    return {tool["name"] for tool in response["result"]["tools"]}, response["result"]["tool_visibility_audit"]


def test_mcp_generic_stdio_discovery_defaults_to_safe_observer_tools():
    names, audit = _stdio_tools_for({"client_id": "generic-mcp"})

    assert names == {
        "validate_eip",
        "query_retrospective",
        "repo_assess",
        "evidence_inspect",
        "provider_inventory",
        "swarm_status",
    }
    assert any(record["tool_name"] == "repo_patch" and record["reason"] == "requires role builder" for record in audit)


@pytest.mark.parametrize(
    ("params", "expected_present", "expected_absent"),
    [
        (
            {"client_id": "claude-code-fixture", "role": "auditor"},
            {"retro_analyze", "submit_retrospective", "validate_eip"},
            {"repo_patch", "run_proofs"},
        ),
        (
            {
                "client_id": "codex-fixture",
                "role": "builder",
                "approved_high_risk_tools": ["repo_patch"],
            },
            {"repo_patch", "validate_eip", "repo_assess"},
            {"run_proofs", "sign_verify"},
        ),
        (
            {"client_id": "vscode-fixture", "role": "verifier"},
            {"run_proofs", "provider_test", "evidence_inspect"},
            {"repo_patch", "retro_analyze"},
        ),
        (
            {"client_id": "opencode-fixture", "role": "builder"},
            {"validate_eip", "repo_assess"},
            {"repo_patch", "repo_run", "sign_verify"},
        ),
    ],
)
def test_mcp_fixture_clients_see_expected_tool_subsets(params, expected_present, expected_absent):
    names, audit = _stdio_tools_for(params)
    audit_by_tool = {record["tool_name"]: record for record in audit}

    assert expected_present <= names
    assert expected_absent.isdisjoint(names)
    for tool_name in expected_absent:
        assert audit_by_tool[tool_name]["visible"] is False


def test_mcp_dangerous_tool_call_without_approval_is_denied(tmp_path):
    db_path = tmp_path / "control-plane.sqlite3"
    response = _stdio_request({
        "jsonrpc": "2.0",
        "id": 21,
        "method": "tools/call",
        "params": {
            "name": "repo_patch",
            "arguments": {
                "client_id": "opencode-fixture",
                "actor_id": "builder-1",
                "role": "builder",
                "run_id": "run-denied",
                "control_plane_db": str(db_path),
            },
        },
    })

    assert response["id"] == 21
    assert response["result"]["isError"] is True
    assert "high-risk tool requires explicit approval" in response["result"]["content"][0]["text"]
    assert response["result"]["mcp_attribution"]["policy_decision"]["allow"] is False
    events = ServerStore(db_path).list_control_plane_events()
    assert [event.status for event in events] == [
        ControlPlaneEventStatus.ACTIVE,
        ControlPlaneEventStatus.BLOCKED,
        ControlPlaneEventStatus.FAILED,
    ]
    assert "high-risk tool requires explicit approval" in events[1].summary
