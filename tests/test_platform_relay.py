import json
from pathlib import Path
import subprocess
import pytest


def test_platform_conf_exists():
    conf_path = Path("hooks/platform/platform.conf")
    assert conf_path.exists()
    content = conf_path.read_text(encoding="utf-8")
    assert "PLATFORM_MODE" in content
    assert "WINDOWS_TO_WSL_TRANSLATE" in content
    assert "PYTHON_LINUX" in content


def test_wsl_relay_script_exists_and_blocks_privacy():
    relay_script = Path("hooks/platform/wsl-relay.sh")
    assert relay_script.exists()

    payload = json.dumps({"tool_name": "Read", "tool_input": {"path": ".env"}})
    res = subprocess.run(
        ["bash", str(relay_script.as_posix()), "hooks/check-read-privacy.sh"],
        input=payload,
        text=True,
        capture_output=True,
    )
    assert res.returncode == 2
    assert "ReadPrivacyGuard Block" in res.stdout or "ReadPrivacyGuard Block" in res.stderr


def test_wsl_relay_script_allows_benign_read():
    relay_script = Path("hooks/platform/wsl-relay.sh")
    payload = json.dumps({"tool_name": "Read", "tool_input": {"path": "README.md"}})
    res = subprocess.run(
        ["bash", str(relay_script.as_posix()), "hooks/check-read-privacy.sh"],
        input=payload,
        text=True,
        capture_output=True,
    )
    assert res.returncode == 0
