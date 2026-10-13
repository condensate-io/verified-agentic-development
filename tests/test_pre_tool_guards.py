import os
import pytest
from pathlib import Path

from vad.integrity.registry import IntegrityRegistry
from vad.plugins.claude_code.hooks.guards import (
    AgentIntegrityGuard,
    CredentialGuard,
    DestructiveBashGuard,
    EIPReadinessGuard,
    GuardDecision,
    ReadPrivacyGuard,
)
from vad.plugins.claude_code.hooks.pre_tool_use import PreToolUseHook


def test_credential_guard_detects_secrets(monkeypatch):
    guard = CredentialGuard()

    # AWS Key in write
    payload = {"content": "const aws = 'AKIA1234567890ABCDEF';"}
    res = guard.evaluate("Write", payload)
    assert res.allowed is False
    assert res.decision == GuardDecision.DENY
    assert "AWS Access Key" in res.reason

    # OpenAI Key
    payload_sk = {"codecontent": "OPENAI_API_KEY = 'sk-1234567890abcdef1234567890abcdef'"}
    res_sk = guard.evaluate("write_to_file", payload_sk)
    assert res_sk.allowed is False

    # Private Key
    payload_pem = {"content": "-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA..."}
    res_pem = guard.evaluate("edit_file", payload_pem)
    assert res_pem.allowed is False

    # Safe payload
    safe_res = guard.evaluate("write_file", {"content": "def add(a, b): return a + b"})
    assert safe_res.allowed is True

    # Escape hatch CREDENTIAL_GUARD=off
    monkeypatch.setenv("CREDENTIAL_GUARD", "off")
    bypassed_res = guard.evaluate("Write", payload)
    assert bypassed_res.allowed is True
    assert bypassed_res.bypassed is True


def test_destructive_bash_guard_tiers(tmp_path, monkeypatch):
    rules_file = tmp_path / "rules.json"
    rules_file.write_text("""{
        "deny": [{"pattern": "rm\\\\s+-[a-zA-Z]*r[a-zA-Z]*f?\\\\s+/(\\\\s|$)", "description": "Wipe root"}],
        "ask": [{"pattern": "git\\\\s+reset\\\\s+--hard", "description": "Hard reset"}]
    }""")

    guard = DestructiveBashGuard(rules_path=str(rules_file.as_posix()))

    # Deny tier
    res_deny = guard.evaluate("bash", {"command": "rm -rf /"})
    assert res_deny.allowed is False
    assert res_deny.decision == GuardDecision.DENY

    # Ask tier
    res_ask = guard.evaluate("bash", {"command": "git reset --hard HEAD~1"})
    assert res_ask.decision == GuardDecision.ASK

    # Benign command
    res_benign = guard.evaluate("bash", {"command": "pytest -q"})
    assert res_benign.allowed is True

    # Escape hatch DESTRUCTIVE_BASH_GUARD=off
    monkeypatch.setenv("DESTRUCTIVE_BASH_GUARD", "off")
    res_bypassed = guard.evaluate("bash", {"command": "rm -rf /"})
    assert res_bypassed.allowed is True
    assert res_bypassed.bypassed is True


def test_read_privacy_guard(monkeypatch):
    guard = ReadPrivacyGuard()

    # Block .env
    assert guard.evaluate("read_file", {"path": ".env"}).allowed is False
    assert guard.evaluate("read_file", {"path": "config/.env.local"}).allowed is False

    # Block pem
    assert guard.evaluate("view_file", {"absolutepath": "/etc/ssl/cert.pem"}).allowed is False

    # Block id_rsa
    assert guard.evaluate("cat", {"command": "cat ~/.ssh/id_rsa"}).allowed is False

    # Safe file
    assert guard.evaluate("read_file", {"path": "src/main.py"}).allowed is True

    # Escape hatch READ_PRIVACY_GUARD=off
    monkeypatch.setenv("READ_PRIVACY_GUARD", "off")
    assert guard.evaluate("read_file", {"path": ".env"}).allowed is True


def test_agent_integrity_guard(tmp_path, monkeypatch):
    reg_file = tmp_path / "registry.json"
    reg = IntegrityRegistry(str(reg_file.as_posix()))

    agent_file = tmp_path / "my_agent.py"
    agent_file.write_text("class MyAgent: pass")
    reg.register(str(agent_file.as_posix()), category="agents")

    guard = AgentIntegrityGuard(registry=reg)

    # Valid agent
    assert guard.evaluate("dispatch", {"path": str(agent_file.as_posix())}).allowed is True

    # Tampered agent
    agent_file.write_text("class TamperedAgent: pass")
    tampered_res = guard.evaluate("dispatch", {"path": str(agent_file.as_posix())})
    assert tampered_res.allowed is False
    assert "failed integrity check" in tampered_res.reason

    # Escape hatch AGENT_INTEGRITY_GUARD=off
    monkeypatch.setenv("AGENT_INTEGRITY_GUARD", "off")
    assert guard.evaluate("dispatch", {"path": str(agent_file.as_posix())}).allowed is True


def test_eip_readiness_guard(monkeypatch):
    guard = EIPReadinessGuard()

    # Draft status -> deny
    draft_eip = {"status": "draft", "open_questions": []}
    assert guard.evaluate({"eip": draft_eip}).allowed is False

    # Open questions -> deny
    ready_with_questions = {"status": "ready", "open_questions": ["What is the DB timeout?"]}
    assert guard.evaluate({"eip": ready_with_questions}).allowed is False

    # Ready with 0 open questions -> allow
    ready_eip = {"status": "ready", "open_questions": []}
    assert guard.evaluate({"eip": ready_eip}).allowed is True

    # Escape hatch EIP_READINESS_GUARD=off
    monkeypatch.setenv("EIP_READINESS_GUARD", "off")
    assert guard.evaluate({"eip": draft_eip}).allowed is True


def test_pre_tool_use_hook_full_integration(tmp_path):
    hook = PreToolUseHook()

    # Test secret denial
    res_secret = hook.process({
        "tool_name": "write_file",
        "tool_input": {"path": "src/auth.py", "content": "AWS_KEY = 'AKIA1234567890ABCDEF'"}
    })
    assert res_secret.permissionDecision == "deny"
    assert res_secret.exit_code == 2

    # Test privacy denial
    res_privacy = hook.process({
        "tool_name": "read_file",
        "tool_input": {"path": ".env.production"}
    })
    assert res_privacy.permissionDecision == "deny"
    assert res_privacy.exit_code == 2

    # Test destructive bash
    res_bash = hook.process({
        "tool_name": "bash",
        "tool_input": {"command": "rm -rf /"}
    })
    assert res_bash.permissionDecision == "deny"
    assert res_bash.exit_code == 2
