#!/bin/bash
set -euo pipefail

# check-agent-integrity.sh
# Hash-checks custom subagent types and configurations against the integrity registry.
# Escape hatch: AGENT_INTEGRITY_GUARD=off

if [ "${AGENT_INTEGRITY_GUARD:-on}" = "off" ] || [ "${AGENT_INTEGRITY_GUARD:-on}" = "OFF" ]; then
    echo "[AgentIntegrityGuard] Escape hatch active (AGENT_INTEGRITY_GUARD=off). Guard bypassed."
    exit 0
fi

REGISTRY_FILE="${1:-skills/registry.json}"
REGISTRY_FILE="${REGISTRY_FILE//\\//}"
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
import sys
from pathlib import Path
from vad.integrity.registry import IntegrityRegistry

registry_path = sys.argv[1]
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

# Check if tool invocation dispatches or launches an agent
agent_ref = tool_input.get("agent_type") or tool_input.get("agent_name") or tool_input.get("agent_file") or tool_input.get("subagent") or ""
if not agent_ref:
    # Check if target file is under agents/ or vad/agents/
    target_path = tool_input.get("path") or tool_input.get("file_path") or ""
    if "agent" in str(target_path).lower():
        agent_ref = str(target_path)

if not agent_ref:
    sys.exit(0)

# Resolve file path for the agent
agent_paths = [
    agent_ref,
    f"agents/{agent_ref}.yaml",
    f"agents/{agent_ref}.json",
    f"agents/{agent_ref}.py",
    f"vad/agents/{agent_ref}.py",
]

found_file = None
for ap in agent_paths:
    p = Path(ap)
    if p.exists() and p.is_file():
        found_file = p
        break

if not found_file:
    # If not a custom file on disk, assume standard built-in
    sys.exit(0)

if not os.path.exists(registry_path):
    print(f"[AgentIntegrityGuard DENY] Registry {registry_path} not found. Cannot verify agent integrity.")
    sys.exit(2)

reg = IntegrityRegistry(registry_path)
valid, err = reg.verify_safe(str(found_file.as_posix()))
if not valid:
    print(f"[AgentIntegrityGuard DENY] Integrity check failed for subagent definition {found_file}: {err}")
    print("Set AGENT_INTEGRITY_GUARD=off to bypass for local test agents.")
    sys.exit(2)

sys.exit(0)
' "$REGISTRY_FILE" <<< "$PAYLOAD"
