#!/bin/bash
set -euo pipefail

# check-credential-guard.sh
# Blocks Write/Edit tool calls if content looks like an embedded secret or credential.
# Escape hatch: CREDENTIAL_GUARD=off

if [ "${CREDENTIAL_GUARD:-on}" = "off" ] || [ "${CREDENTIAL_GUARD:-on}" = "OFF" ]; then
    echo "[CredentialGuard] Escape hatch active (CREDENTIAL_GUARD=off). Guard bypassed."
    exit 0
fi

# Read JSON payload from stdin
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
import json
import re
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

# Check if action is write/edit
write_tools = {"write", "edit", "write_file", "edit_file", "str_replace_editor", "create_file", "write_to_file"}
is_write = any(w in tool_name for w in write_tools)

content = ""
for k in ("content", "new_string", "file_text", "text", "body", "replacementcontent", "codecontent"):
    if k in tool_input and isinstance(tool_input[k], str):
        content += "\n" + tool_input[k]

if not is_write or not content:
    sys.exit(0)

SECRET_PATTERNS = [
    (r"-----BEGIN\s+(?:[A-Z0-9_-]+\s+)*PRIVATE KEY", "Private cryptographic key block"),
    (r"AKIA[0-9A-Z]{16}", "AWS Access Key ID"),
    (r"sk-[a-zA-Z0-9_-]{20,}", "OpenAI API Secret Key"),
    (r"(?:ghp|gho|ghu|ghs|ghr)_[a-zA-Z0-9]{36}", "GitHub Personal Access Token"),
    (r"xox[baprs]-[0-9a-zA-Z]{10,}", "Slack API Token"),
    (r"(?:api[_-]?key|secret[_-]?key|auth[_-]?token|password)\s*[:=]\s*[\"'\"][A-Za-z0-9+/=_.!-]{12,}[\"'\"]", "High-entropy credential assignment"),
]

for pattern, desc in SECRET_PATTERNS:
    if re.search(pattern, content, re.IGNORECASE):
        print(f"[CredentialGuard Block] Potential secret detected in Write/Edit payload: {desc}")
        print("Set CREDENTIAL_GUARD=off to bypass if this is an intentional non-production fixture.")
        sys.exit(2)

sys.exit(0)
' <<< "$PAYLOAD"
