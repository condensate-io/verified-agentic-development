from typing import Dict, Optional
from vad.memory.contracts import MemoryEntry, MemoryScope

class LocalMemoryStore:
    def __init__(self):
        self._store: Dict[str, MemoryEntry] = {}

    def save(self, entry: MemoryEntry):
        if len(entry.content.encode('utf-8')) > entry.scope.max_payload_size:
            raise ValueError("Payload size exceeds scope limit")
        self._store[entry.id] = entry

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
