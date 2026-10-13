"""SkillGuard Integrity Registry: Computes and stores SHA-256 checksums to prevent unauthorized modifications.

Extended with verdict schema, reports, risk scores, conditions, and separate sections
for skills, agents, commands, and hooks.
"""

from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from pydantic import BaseModel, Field


class IntegrityViolation(Exception):
    """Raised when a file's runtime checksum does not match the registered baseline."""
    pass


class RegistryVerdict(str, Enum):
    ALLOW = "ALLOW"
    REJECT = "REJECT"
    CONDITIONAL = "CONDITIONAL"
    BOOTSTRAP = "BOOTSTRAP"


class RegistryCategory(str, Enum):
    SKILLS = "skills"
    AGENTS = "agents"
    COMMANDS = "commands"
    HOOKS = "hooks"


class RegistryEntry(BaseModel):
    path: str
    sha256: str
    registered_at: str
    category: str = "skills"
    size_bytes: int = 0
    verdict: str = "ALLOW"  # ALLOW | REJECT | CONDITIONAL | BOOTSTRAP
    report: str = ""
    risk_score: float = 0.0
    conditions: List[str] = Field(default_factory=list)


class IntegrityRegistry:
    """Tamper-evident registry tracking SHA-256 hashes and governance verdicts of skills, agents, commands, and hooks."""

    VALID_SECTIONS = ("skills", "agents", "commands", "hooks")

    def __init__(self, registry_path: str = "skills/registry.json"):
        self.registry_path = registry_path
        self._sections: Dict[str, Dict[str, RegistryEntry]] = {
            s: {} for s in self.VALID_SECTIONS
        }
        self.load()

    @property
    def _entries(self) -> Dict[str, RegistryEntry]:
        """Flattened dictionary of all registered paths across all sections."""
        flat = {}
        for sec in self.VALID_SECTIONS:
            flat.update(self._sections[sec])
        return flat

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

    def _normalize_category(self, category: str, path: str = "") -> str:
        cat = category.lower().strip()
        if cat in ("skill", "skills"):
            return "skills"
        if cat in ("agent", "agents"):
            return "agents"
        if cat in ("command", "commands"):
            return "commands"
        if cat in ("hook", "hooks"):
            return "hooks"

        # Heuristic detection based on path
        lower_p = path.lower()
        if "agent" in lower_p:
            return "agents"
        if "hook" in lower_p:
            return "hooks"
        if "command" in lower_p:
            return "commands"
        return "skills"

    def register(
        self,
        file_path: str,
        category: str = "skills",
        verdict: str = "ALLOW",
        report: str = "Standard verified entry",
        risk_score: float = 0.0,
        conditions: Optional[List[str]] = None,
    ) -> RegistryEntry:
        p = Path(file_path)
        sha = self.compute_sha256(file_path)
        size = p.stat().st_size
        now_str = datetime.now(timezone.utc).isoformat()
        norm_path = str(p.as_posix())

        sec = self._normalize_category(category, norm_path)
        entry = RegistryEntry(
            path=norm_path,
            sha256=sha,
            registered_at=now_str,
            category=sec,
            size_bytes=size,
            verdict=verdict.upper(),
            report=report,
            risk_score=float(risk_score),
            conditions=conditions or [],
        )

        # Remove from other sections if previously present
        for other_sec in self.VALID_SECTIONS:
            self._sections[other_sec].pop(norm_path, None)

        self._sections[sec][norm_path] = entry
        self.save()
        return entry

    def get_section(self, section: str) -> Dict[str, RegistryEntry]:
        norm_sec = self._normalize_category(section)
        return dict(self._sections.get(norm_sec, {}))

    def get_entry(self, file_path: str) -> Optional[RegistryEntry]:
        p = Path(file_path)
        norm_path = str(p.as_posix())
        return self._entries.get(norm_path)

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

        if entry.verdict == "REJECT":
            return False, f"Rejected artifact: '{norm_path}' has verdict REJECT. Reason: {entry.report}"

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

            # Check if this is sectioned or flat
            is_sectioned = any(k in self.VALID_SECTIONS for k in data.keys()) and not any(
                "sha256" in v for v in data.values() if isinstance(v, dict)
            )

            if is_sectioned:
                for sec in self.VALID_SECTIONS:
                    sec_data = data.get(sec, {})
                    self._sections[sec] = {k: RegistryEntry(**v) for k, v in sec_data.items()}
            else:
                # Migrate legacy flat format
                for path_str, entry_data in data.items():
                    entry = RegistryEntry(**entry_data)
                    sec = self._normalize_category(entry.category, entry.path)
                    entry.category = sec
                    self._sections[sec][entry.path] = entry
        except Exception:
            self._sections = {s: {} for s in self.VALID_SECTIONS}

    def save(self) -> None:
        p = Path(self.registry_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        data = {
            sec: {k: v.model_dump() for k, v in entries.items()}
            for sec, entries in self._sections.items()
        }
        with open(p, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
