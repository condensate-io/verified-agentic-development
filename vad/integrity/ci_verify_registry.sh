#!/bin/bash
set -euo pipefail

# CI verification gate: Fails pipeline if any registered artifact has drifted or been tampered with
REGISTRY_FILE="${1:-skills/registry.json}"

python3 -c '
import sys
from vad.integrity.registry import IntegrityRegistry, IntegrityViolation

registry_path = sys.argv[1]
reg = IntegrityRegistry(registry_path)
entries = reg.get_all()

if not entries:
    print(f"Warning: No entries found in {registry_path}")
    sys.exit(0)

drifted = []
for path_str, entry in entries.items():
    valid, err = reg.verify_safe(path_str)
    if not valid:
        drifted.append((path_str, err))

if drifted:
    print("=" * 60)
    print("CI INTEGRITY VERIFICATION FAILED: ARTIFACT HASH DRIFT DETECTED")
    print("=" * 60)
    for p, err in drifted:
        print(f"  [DRIFT] {p}: {err}")
    print("\nPlease run vad/integrity/bootstrap_registry.sh after verifying legitimate changes.")
    sys.exit(1)

print(f"CI Integrity Gate Passed: {len(entries)} artifacts verified successfully against baseline.")
' "$REGISTRY_FILE"
