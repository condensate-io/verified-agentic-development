"""Runtime integrity checker: Gatekeeper verifying SHA-256 integrity before tool or skill execution."""

from pathlib import Path
from typing import List, Optional
from pydantic import BaseModel
from vad.integrity.registry import IntegrityRegistry


class IntegrityCheckResult(BaseModel):
    path: str
    decision: str  # "allow" or "block"
    reason: str
    sha256: Optional[str] = None
    is_verified: bool


class RuntimeIntegrityChecker:
    """Invoked by PreToolUse hooks to verify that targeted skills/scripts have not been tampered with."""

    def __init__(self, registry: Optional[IntegrityRegistry] = None, enforce: bool = True):
        self.registry = registry or IntegrityRegistry()
        self.enforce = enforce

    def check(self, file_path: str) -> IntegrityCheckResult:
        p = Path(file_path)
        if not p.exists() or not p.is_file():
            # If path does not point to a local file, allow non-file commands
            return IntegrityCheckResult(
                path=file_path,
                decision="allow",
                reason="Path is not a governed file",
                is_verified=True,
            )

        valid, err = self.registry.verify_safe(file_path)
        if not valid:
            if self.enforce:
                return IntegrityCheckResult(
                    path=file_path,
                    decision="block",
                    reason=err or "Integrity check failed",
                    is_verified=False,
                )
            else:
                return IntegrityCheckResult(
                    path=file_path,
                    decision="allow",
                    reason=f"Warning (enforce=False): {err}",
                    is_verified=False,
                )

        sha = self.registry.compute_sha256(file_path)
        return IntegrityCheckResult(
            path=file_path,
            decision="allow",
            reason="Integrity verified: SHA-256 matches baseline",
            sha256=sha,
            is_verified=True,
        )

    def check_command_paths(self, args: List[str]) -> List[IntegrityCheckResult]:
        """Inspects command arguments and evaluates any file paths ending in governed extensions."""
        results = []
        governed_extensions = {".sh", ".py", ".md", ".json", ".yaml", ".yml"}

        for arg in args:
            clean = arg.strip().strip("'\"")
            p = Path(clean)
            if p.suffix in governed_extensions and p.exists() and p.is_file():
                results.append(self.check(clean))

        return results
