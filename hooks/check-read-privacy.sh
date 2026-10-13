#!/bin/bash
set -euo pipefail

# check-read-privacy.sh
# Blocks tool reads targeting sensitive files (.env*, *.pem, id_rsa*).
# Escape hatch: READ_PRIVACY_GUARD=off

if [ "${READ_PRIVACY_GUARD:-on}" = "off" ] || [ "${READ_PRIVACY_GUARD:-on}" = "OFF" ]; then
    echo "[ReadPrivacyGuard] Escape hatch active (READ_PRIVACY_GUARD=off). Guard bypassed."
    exit 0
fi

PAYLOAD=$(cat)

if [ -z "$PAYLOAD" ]; then
    exit 0
fi

# Detect python interpreter
if command -v python3 >/dev/null 2>&1; then
    PY="python3"
elif command -v python >/dev/null 2>&1; then
    PY="python"
elif command -v python.exe >/dev/null 2>&1; then
    PY="python.exe"
else
    PY="python"
fi

"$PY" -c '
import fnmatch
import json
import os
import sys

raw = sys.stdin.read()
if not raw.strip():
    sys.exit(0)

try:
    data = json.loads(raw)
except Exception:
    sys.exit(0)

tool_name = str(data.get("tool_name", "")).lower()
tool_input = data.get("tool_input", {})
if not isinstance(tool_input, dict):
    tool_input = {}

# Check read-related tools or command inspect
read_tools = {"read", "view", "read_file", "view_file", "cat"}
is_read_tool = any(r in tool_name for r in read_tools)

target_path = tool_input.get("path") or tool_input.get("file_path") or tool_input.get("target") or tool_input.get("filename") or ""

# Also inspect commands like cat, head, tail, grep on bash calls
cmd = tool_input.get("command") or ""

paths_to_check = []
if target_path:
    paths_to_check.append(str(target_path))

if cmd:
    parts = cmd.split()
    for p in parts:
        paths_to_check.append(p)

BLOCKED_PATTERNS = [".env*", "*.pem", "id_rsa*", "*id_dsa*", "*id_ecdsa*", "*id_ed25519*"]

for p in paths_to_check:
    base = os.path.basename(p.strip("\"'\''"))
    for pattern in BLOCKED_PATTERNS:
        if fnmatch.fnmatch(base, pattern):
            print(f"[ReadPrivacyGuard Block] Access to privacy-sensitive file blocked: {p} matches {pattern}")
            print("Set READ_PRIVACY_GUARD=off to bypass if explicitly required for credential rotation.")
            sys.exit(2)

sys.exit(0)
' <<< "$PAYLOAD"
