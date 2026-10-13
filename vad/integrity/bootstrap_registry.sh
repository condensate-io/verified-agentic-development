#!/bin/bash
set -euo pipefail

# Bootstraps skills/registry.json with SHA-256 hashes and structured sections for all governed artifacts
REGISTRY_FILE="${1:-skills/registry.json}"
REGISTRY_FILE="${REGISTRY_FILE//\\//}"
DEFAULT_VERDICT="${2:-ALLOW}"
SEARCH_DIRS=("vad/agents" "vad/skills" "vad/plugins" "vad/guards" "policies" "hooks" "agents" "skills")
export PYTHONPATH="${PYTHONPATH:-.}:."

if command -v python3 >/dev/null 2>&1 && python3 -c 'import pydantic' >/dev/null 2>&1; then
    PY_BIN="python3"
elif command -v python >/dev/null 2>&1 && python -c 'import pydantic' >/dev/null 2>&1; then
    PY_BIN="python"
elif command -v python.exe >/dev/null 2>&1; then
    PY_BIN="python.exe"
else
    PY_BIN="python3"
fi

mkdir -p "$(dirname "$REGISTRY_FILE")"

"$PY_BIN" -c '
import sys
from pathlib import Path
from vad.integrity.registry import IntegrityRegistry

registry_path = sys.argv[1]
default_verdict = sys.argv[2]
search_dirs = sys.argv[3:]

reg = IntegrityRegistry(registry_path)
registered_count = 0

for d_str in search_dirs:
    p = Path(d_str)
    if not p.exists():
        continue
    for file_path in p.rglob("*"):
        if file_path.is_file() and file_path.suffix in [".py", ".sh", ".md", ".json", ".yaml", ".yml", ".rego"]:
            if "__pycache__" in str(file_path) or ".pytest_cache" in str(file_path):
                continue
            # Avoid registering the registry file itself
            if file_path.resolve() == Path(registry_path).resolve():
                continue
            
            p_str = str(file_path.as_posix())
            if "agent" in p_str:
                cat = "agents"
            elif "hook" in p_str:
                cat = "hooks"
            elif "command" in p_str:
                cat = "commands"
            else:
                cat = "skills"

            reg.register(
                p_str,
                category=cat,
                verdict=default_verdict,
                report=f"Bootstrapped {cat} baseline with verdict {default_verdict}",
                risk_score=5.0 if default_verdict == "ALLOW" else 0.0,
            )
            registered_count += 1

print(f"Successfully bootstrapped {registered_count} artifacts into {registry_path} with verdict {default_verdict}")
' "$REGISTRY_FILE" "$DEFAULT_VERDICT" "${SEARCH_DIRS[@]}"
