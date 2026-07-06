import json
import re
import threading
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from vad.policy.engine import PolicyEngine
from vad.repo.patch_apply import apply_unified_diff
from vad.server.serve import prepare_ui_server


REPO_ROOT = Path(__file__).resolve().parents[1]
SECRET_VALUE_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_-]{16,}"),
    re.compile(r"api[_-]?key\s*[:=]\s*['\"]?[^'\"\s]+", re.IGNORECASE),
    re.compile(r"password\s*[:=]\s*['\"]?[^'\"\s]+", re.IGNORECASE),
    re.compile(r"private[_-]?key\s*[:=]", re.IGNORECASE),
    re.compile(r"token\s*=\s*['\"]?[^'\"\s]+", re.IGNORECASE),
]
LOCAL_NETWORK_TESTS = {
    "tests/test_server_api.py",
    "tests/test_control_plane_docker.py",
    "tests/test_security_audit.py",
    "tests/test_ui.py",
    "tests/test_ui_docker.py",
}


def test_security_audit_blocks_filesystem_write_escape(tmp_path):
    inside = tmp_path / "src" / "app.py"
    inside.parent.mkdir()
    inside.write_text("unchanged\n", encoding="utf-8")
    outside = tmp_path.parent / "outside-security-audit.py"
    outside.write_text("do-not-change\n", encoding="utf-8")
    patch = """--- a/../outside-security-audit.py
+++ b/../outside-security-audit.py
@@ -1,1 +1,1 @@
-do-not-change
+changed
"""

    result = apply_unified_diff(tmp_path, patch)

    assert result.applied is False
    assert result.blocker == "path escapes workspace root"
    assert outside.read_text(encoding="utf-8") == "do-not-change\n"


def test_security_audit_fixtures_do_not_contain_secret_values():
    scanned_files = [
        path
        for root in [REPO_ROOT / "examples" / "level3-demo"]
        for path in root.rglob("*")
        if path.is_file() and path.suffix.lower() in {".json", ".yaml", ".yml", ".md", ".txt", ".py"}
    ]

    findings = []
    for path in scanned_files:
        text = path.read_text(encoding="utf-8")
        for pattern in SECRET_VALUE_PATTERNS:
            if pattern.search(text):
                findings.append(f"{path.relative_to(REPO_ROOT)} matched {pattern.pattern}")

    assert scanned_files
    assert findings == []


def test_security_audit_default_tests_only_use_local_network_endpoints():
    findings = []
    for path in sorted((REPO_ROOT / "tests").glob("test_*.py")):
        relative = path.relative_to(REPO_ROOT).as_posix()
        text = path.read_text(encoding="utf-8")
        network_tokens = ["urlopen", "socket.socket", "import requests", "from requests"]
        if any(token in text for token in network_tokens) and relative not in LOCAL_NETWORK_TESTS:
            findings.append(f"{relative} uses network primitive outside local allowlist")
        for match in re.finditer(r"https?://([^/\"'\s)]+)", text):
            host = match.group(1).split(":", 1)[0]
            if host not in {"127.0.0.1", "localhost"} and "{" not in host:
                findings.append(f"{relative} contains non-local URL {match.group(0)}")
            if "{" in host and relative not in LOCAL_NETWORK_TESTS:
                findings.append(f"{relative} contains dynamic URL host {match.group(0)}")

    assert findings == []


def test_security_audit_ui_actions_cannot_bypass_backend_policy(tmp_path):
    server = prepare_ui_server(
        "127.0.0.1",
        0,
        tmp_path / "evidence",
        tmp_path / "vad.sqlite3",
        tmp_path / "ui",
        seed_level3_demo=True,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address[:2]
        request = Request(
            f"http://{host}:{port}/actions/approve",
            data=json.dumps({
                "run_id": "level3-demo-success",
                "actor": "claude-code-builder",
                "actor_role": "release_guardian",
                "action": "approve_release",
            }).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            urlopen(request)
        except HTTPError as response:
            payload = json.loads(response.read())
            assert response.code == 403
        else:
            raise AssertionError("self approval unexpectedly succeeded")

        assert payload["approval"]["decision"]["allow"] is False
        assert payload["approval"]["decision"]["denials"] == ["builder may not approve own run"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_security_audit_production_deployment_apply_requires_approval():
    decision = PolicyEngine().evaluate_deployment(
        action="deploy_apply",
        environment="production",
        approval_ref=None,
        telemetry_count=1,
        rollback_enabled=True,
    )

    assert decision.allow is False
    assert decision.requires_human is True
    assert "production deployment requires approval" in decision.denials
