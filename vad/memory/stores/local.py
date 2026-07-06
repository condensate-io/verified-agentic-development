import json
from pathlib import Path
from typing import Dict, Optional
from vad.memory.contracts import MemoryEntry, MemoryScope

class LocalMemoryStore:
    def __init__(self, file_path: str | None = None):
        self._store: Dict[str, MemoryEntry] = {}
        self.file_path = Path(file_path) if file_path else None
        if self.file_path and self.file_path.exists():
            self._load()

    def save(self, entry: MemoryEntry):
        if len(entry.content.encode('utf-8')) > entry.scope.max_payload_size:
            raise ValueError("Payload size exceeds scope limit")
        self._store[entry.id] = entry
        self._persist()

    def retrieve(self, entry_id: str, scope: MemoryScope) -> Optional[MemoryEntry]:
        entry = self._store.get(entry_id)
        if not entry:
            return None
        if entry.scope.owner != scope.owner or entry.scope.purpose != scope.purpose:
            raise PermissionError("Scope mismatch")
        return entry

    def get_by_scope(self, scope: MemoryScope) -> list[MemoryEntry]:
        return [
            entry for entry in self._store.values()
            if entry.scope.owner == scope.owner and entry.scope.purpose == scope.purpose
        ]

    def _load(self):
        data = json.loads(self.file_path.read_text())
        self._store = {
            entry_id: MemoryEntry(**entry_data)
            for entry_id, entry_data in data.items()
        }

    def _persist(self):
        if not self.file_path:
            return
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        self.file_path.write_text(json.dumps(
            {entry_id: entry.model_dump(mode="json") for entry_id, entry in self._store.items()},
            indent=2,
            sort_keys=True,
        ))
