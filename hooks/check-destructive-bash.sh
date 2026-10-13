#!/bin/bash
set -euo pipefail

# check-destructive-bash.sh
# Reads data-driven destructive-bash-rules.json with separate deny and ask tiers.
# Escape hatch: DESTRUCTIVE_BASH_GUARD=off

if [ "${DESTRUCTIVE_BASH_GUARD:-on}" = "off" ] || [ "${DESTRUCTIVE_BASH_GUARD:-on}" = "OFF" ]; then
    echo "[DestructiveBashGuard] Escape hatch active (DESTRUCTIVE_BASH_GUARD=off). Guard bypassed."
    exit 0
fi

RULES_FILE="${1:-hooks/destructive-bash-rules.json}"
RULES_FILE="${RULES_FILE//\\//}"
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
import os
import re
import sys

rules_path = sys.argv[1]
raw = sys.stdin.read()
if not raw.strip():
    sys.exit(0)

try:
    payload = json.loads(raw)
except Exception:
    sys.exit(0)

tool_name = str(payload.get("tool_name", "")).lower()
tool_input = payload.get("tool_input", {})
if not isinstance(tool_input, dict):
    tool_input = {}

cmd = tool_input.get("command") or tool_input.get("cmd") or ""
if not cmd or not any(b in tool_name for b in ("bash", "sh", "terminal", "exec", "run")):
    sys.exit(0)

if not os.path.exists(rules_path):
    # Fallback to default search path
    alt = os.path.join(os.path.dirname(__file__) if "__file__" in locals() else ".", "destructive-bash-rules.json")
    if os.path.exists(alt):
        rules_path = alt

rules = {"deny": [], "ask": []}
if os.path.exists(rules_path):
    with open(rules_path, "r", encoding="utf-8") as f:
        rules = json.load(f)

# 1. Deny tier check
for rule in rules.get("deny", []):
    pat = rule.get("pattern", "")
    if pat and re.search(pat, cmd, re.IGNORECASE):
        desc = rule.get("description", "Prohibited destructive operation")
        print(f"[DestructiveBashGuard DENY] Command matches deny-tier rule: {desc}")
        print(f"Command: {cmd}")
        print("Set DESTRUCTIVE_BASH_GUARD=off to bypass if authorized.")
        sys.exit(2)

# 2. Ask tier check
for rule in rules.get("ask", []):
    pat = rule.get("pattern", "")
    if pat and re.search(pat, cmd, re.IGNORECASE):
        desc = rule.get("description", "Risky shell operation")
        print(f"[DestructiveBashGuard ASK] Command matches ask-tier rule: {desc}")
        print(f"Command: {cmd}")
        print("Requires explicit operator confirmation before execution.")
        sys.exit(1)

sys.exit(0)
' "$RULES_FILE" <<< "$PAYLOAD"
