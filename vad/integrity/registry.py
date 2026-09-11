"""SkillGuard Integrity Registry: Computes and stores SHA-256 checksums to prevent unauthorized modifications."""

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from pydantic import BaseModel, Field


class IntegrityViolation(Exception):
    """Raised when a file's runtime checksum does not match the registered baseline."""
    pass


class RegistryEntry(BaseModel):
    path: str
    sha256: str
    registered_at: str
    category: str = "skill"
    size_bytes: int = 0


class IntegrityRegistry:
    """Tamper-evident registry tracking SHA-256 hashes of governed skills, agents, and hooks."""

    def __init__(self, registry_path: str = "skills/registry.json"):
        self.registry_path = registry_path
        self._entries: Dict[str, RegistryEntry] = {}
        self.load()

    @staticmethod
    def compute_sha256(file_path: str) -> str:
        p = Path(file_path)
        if not p.exists() or not p.is_file():
            raise FileNotFoundError(f"File not found: {file_path}")
        h = hashlib.sha256()
        with open(p, "rb") as f:
            while chunk := f.read(65536):
                h.update(chunk)
        return h.hexdigest()

    def register(self, file_path: str, category: str = "skill") -> RegistryEntry:
        p = Path(file_path)
        sha = self.compute_sha256(file_path)
        size = p.stat().st_size
        now_str = datetime.now(timezone.utc).isoformat()

        # Normalize relative path representation
        norm_path = str(p.as_posix())
        entry = RegistryEntry(
            path=norm_path,
            sha256=sha,
            registered_at=now_str,
            category=category,
            size_bytes=size,
        )
        self._entries[norm_path] = entry
        self.save()
        return entry

    def verify_safe(self, file_path: str) -> Tuple[bool, Optional[str]]:
        p = Path(file_path)
        norm_path = str(p.as_posix())

        entry = self._entries.get(norm_path)
        if not entry:
            return False, f"Unregistered artifact: '{norm_path}' not present in integrity registry"

        try:
            current_sha = self.compute_sha256(file_path)
        except Exception as e:
            return False, f"Could not read file: {str(e)}"

        if current_sha != entry.sha256:
            return False, (
                f"Integrity violation on '{norm_path}': expected {entry.sha256[:12]}..., "
                f"actual {current_sha[:12]}..."
            )

        return True, None

    def verify(self, file_path: str) -> bool:
        valid, err = self.verify_safe(file_path)
        if not valid:
            raise IntegrityViolation(err)
        return True

    def get_all(self) -> Dict[str, RegistryEntry]:
        return dict(self._entries)

    def load(self) -> None:
        p = Path(self.registry_path)
        if not p.exists():
            return
        try:
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._entries = {k: RegistryEntry(**v) for k, v in data.items()}
        except Exception:
            self._entries = {}

    def save(self) -> None:
        p = Path(self.registry_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        data = {k: v.model_dump() for k, v in self._entries.items()}
        with open(p, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
