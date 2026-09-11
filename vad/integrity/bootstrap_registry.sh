#!/bin/bash
set -euo pipefail

# Bootstraps skills/registry.json with SHA-256 hashes of all governed artifacts
REGISTRY_FILE="${1:-skills/registry.json}"
SEARCH_DIRS=("vad/agents" "vad/skills" "vad/plugins" "vad/guards" "policies")

mkdir -p "$(dirname "$REGISTRY_FILE")"

python3 -c '
import sys
from pathlib import Path
from vad.integrity.registry import IntegrityRegistry

registry_path = sys.argv[1]
reg = IntegrityRegistry(registry_path)

search_dirs = sys.argv[2:]
registered_count = 0

for d_str in search_dirs:
    p = Path(d_str)
    if not p.exists():
        continue
    for file_path in p.rglob("*"):
        if file_path.is_file() and file_path.suffix in [".py", ".sh", ".md", ".json", ".yaml", ".yml", ".rego"]:
            # Avoid registering the registry file itself
            if file_path.resolve() == Path(registry_path).resolve():
                continue
            cat = "skill" if "skill" in str(file_path) else ("agent" if "agent" in str(file_path) else "governance")
            reg.register(str(file_path.as_posix()), category=cat)
            registered_count += 1

print(f"Successfully bootstrapped {registered_count} artifacts into {registry_path}")
' "$REGISTRY_FILE" "${SEARCH_DIRS[@]}"
