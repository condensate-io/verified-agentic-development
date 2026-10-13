#!/bin/bash
set -euo pipefail

# check-eip-readiness.sh
# Blocks tool dispatch referencing an EIP unless the EIP has status: ready and no open questions.
# Escape hatch: EIP_READINESS_GUARD=off

if [ "${EIP_READINESS_GUARD:-on}" = "off" ] || [ "${EIP_READINESS_GUARD:-on}" = "OFF" ]; then
    echo "[EIPReadinessGuard] Escape hatch active (EIP_READINESS_GUARD=off). Guard bypassed."
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
import json
import os
import sys
from pathlib import Path

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

eip_ref = payload.get("eip") or tool_input.get("eip") or tool_input.get("eip_path") or tool_input.get("eip_file") or ""
if not eip_ref:
    sys.exit(0)

eip_dict = None
if isinstance(eip_ref, dict):
    eip_dict = eip_ref
elif isinstance(eip_ref, str):
    p = Path(eip_ref)
    if p.exists() and p.is_file():
        try:
            with open(p, "r", encoding="utf-8") as f:
                if p.suffix in (".yaml", ".yml"):
                    import yaml
                    eip_dict = yaml.safe_load(f)
                else:
                    eip_dict = json.load(f)
        except Exception:
            pass

if not eip_dict:
    # If unparseable or missing file, fail readiness gate
    print(f"[EIPReadinessGuard DENY] Unparseable or missing EIP artifact: {eip_ref}")
    sys.exit(2)

status = str(eip_dict.get("status", "")).lower().strip()
open_questions = eip_dict.get("open_questions", [])

if status != "ready":
    print(f"[EIPReadinessGuard DENY] EIP is not ready for dispatch: current status is \x27{status}\x27 (must be \x27ready\x27)")
    print("Set EIP_READINESS_GUARD=off to bypass for early exploratory runs.")
    sys.exit(2)

if open_questions and len(open_questions) > 0:
    print(f"[EIPReadinessGuard DENY] EIP has {len(open_questions)} unresolved open question(s):")
    for q in open_questions[:5]:
        print(f"  - {q}")
    print("All open questions must be resolved before builder dispatch.")
    print("Set EIP_READINESS_GUARD=off to bypass for exploratory spikes.")
    sys.exit(2)

sys.exit(0)
' <<< "$PAYLOAD"
