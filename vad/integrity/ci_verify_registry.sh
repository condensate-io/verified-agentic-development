#!/bin/bash
set -euo pipefail

# CI Verification Gate: Flags missing, drifted, REJECT, and BOOTSTRAP-only entries
REGISTRY_FILE="${1:-skills/registry.json}"
REGISTRY_FILE="${REGISTRY_FILE//\\//}"
ALLOW_BOOTSTRAP="${ALLOW_BOOTSTRAP:-0}"
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

"$PY_BIN" -c '
import os
import sys
from pathlib import Path
from vad.integrity.registry import IntegrityRegistry

registry_path = sys.argv[1]
allow_bootstrap = sys.argv[2] in ("1", "true", "True", "yes")

if not os.path.exists(registry_path):
    print(f"ERROR: Integrity registry not found at: {registry_path}")
    print("Run vad/integrity/bootstrap_registry.sh to initialize registry.")
    sys.exit(1)

reg = IntegrityRegistry(registry_path)
all_entries = reg.get_all()

if not all_entries:
    print(f"ERROR: Integrity registry {registry_path} is empty.")
    sys.exit(1)

# Scan for expected governed files on disk
SEARCH_DIRS = ["vad/agents", "vad/skills", "vad/plugins", "vad/guards", "hooks", "agents", "skills"]
governed_extensions = [".py", ".sh", ".md", ".json", ".yaml", ".yml"]

governed_on_disk = set()
for d in SEARCH_DIRS:
    p = Path(d)
    if not p.exists():
        continue
    for f in p.rglob("*"):
        if f.is_file() and f.suffix in governed_extensions:
            # Exclude registry itself and test artifacts
            if f.resolve() == Path(registry_path).resolve() or "__pycache__" in str(f) or ".pytest_cache" in str(f):
                continue
            norm = str(f.as_posix())
            governed_on_disk.add(norm)

# 1. Missing entries
missing = []
for on_disk in sorted(governed_on_disk):
    if on_disk not in all_entries:
        missing.append(on_disk)

# 2. Drifted entries
drifted = []
# 3. REJECT entries
rejected = []
# 4. BOOTSTRAP-only entries
bootstrapped = []

for path_str, entry in sorted(all_entries.items()):
    # Check drift if file still exists
    if not os.path.exists(path_str):
        drifted.append((path_str, "Registered file missing from disk"))
        continue

    valid, err = reg.verify_safe(path_str)
    if not valid and entry.verdict.upper() != "REJECT":
        drifted.append((path_str, err or "Integrity verification failed"))

    verdict = entry.verdict.upper()
    if verdict == "REJECT":
        rejected.append((path_str, entry.report))
    elif verdict == "BOOTSTRAP":
        if not allow_bootstrap:
            bootstrapped.append((path_str, "Unreviewed BOOTSTRAP status not permitted in CI"))

has_errors = False

print("=" * 70)
print("SKILL-GUARD CI REGISTRY INTEGRITY AUDIT")
print("=" * 70)

if missing:
    has_errors = True
    print(f"\n[!] MISSING REGISTRY ENTRIES ({len(missing)}):")
    for m in missing[:20]:
        print(f"    - {m}")
    if len(missing) > 20:
        print(f"    ... and {len(missing) - 20} more.")

if drifted:
    has_errors = True
    print(f"\n[!] DRIFTED ARTIFACTS ({len(drifted)}):")
    for p, err in drifted:
        print(f"    - {p}: {err}")

if rejected:
    has_errors = True
    print(f"\n[!] REJECTED ARTIFACTS IN REGISTRY ({len(rejected)}):")
    for p, reason in rejected:
        print(f"    - {p}: {reason}")

if bootstrapped:
    has_errors = True
    print(f"\n[!] UNREVIEWED BOOTSTRAP-ONLY ARTIFACTS ({len(bootstrapped)}):")
    for p, reason in bootstrapped:
        print(f"    - {p}: {reason}")

if has_errors:
    print("\n" + "=" * 70)
    print("CI INTEGRITY VERIFICATION FAILED: ARTIFACT DRIFT OR POLICY VIOLATION DETECTED")
    print("Run `vad/integrity/bootstrap_registry.sh` or update verdicts with skill-guard reviewer.")
    print("=" * 70)
    sys.exit(1)

print(f"\n[PASS] CI Integrity Gate Passed: {len(all_entries)} artifacts verified successfully against baseline.")
print("=" * 70)
' "$REGISTRY_FILE" "$ALLOW_BOOTSTRAP"
