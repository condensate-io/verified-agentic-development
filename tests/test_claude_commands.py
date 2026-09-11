import pytest
from pathlib import Path
from vad.plugins.claude_code.commands import VADCommandHandler, CommandResult


def test_command_handler_empty():
    handler = VADCommandHandler()
    res = handler.handle("")
    assert res.status == "error"


def test_command_handler_unknown():
    handler = VADCommandHandler()
    res = handler.handle("/vad unknown-command")
    assert res.status == "error"
    assert "Unknown command" in res.message


def test_command_handler_eip_init():
    handler = VADCommandHandler()
    res = handler.handle("/vad eip-init telemetry-fix")
    assert res.status == "success"
    assert "telemetry-fix" in res.message
    assert res.data["eip_id"] == "eip-telemetry-fix"


def test_command_handler_proof_map():
    handler = VADCommandHandler()
    res = handler.handle("/vad proof-map")
    assert res.status == "success"
    assert len(res.data["obligations"]) > 0


def test_command_handler_dor_check():
    handler = VADCommandHandler()
    res = handler.handle("/vad dor-check")
    assert res.status == "success"
    assert res.data["status"] == "admit"


def test_command_handler_review():
    handler = VADCommandHandler()
    res = handler.handle("/vad review cursor")
    assert res.status == "success"
    assert res.data["target_harness"] == "cursor"


def test_command_handler_evidence_inspect(tmp_path):
    ev_dir = tmp_path / "evidence"
    ev_dir.mkdir()
    (ev_dir / "A1.json").write_text('{"id": "A1", "status": "passed"}')
    
    handler = VADCommandHandler(workspace_root=str(tmp_path))
    res = handler.handle("/vad evidence-inspect")
    assert res.status == "success"
    assert res.data["count"] == 1
    assert res.data["evidence"][0]["id"] == "A1"


def test_command_handler_parallel_dispatch_and_onboard():
    handler = VADCommandHandler()
    res1 = handler.handle("/vad parallel-dispatch")
    assert res1.status == "success"
    
    res2 = handler.handle("/vad onboard-repo my-service")
    assert res2.status == "success"
    assert res2.data["repo"] == "my-service"
