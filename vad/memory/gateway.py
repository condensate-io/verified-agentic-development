from typing import Optional
from vad.memory.contracts import MemoryEntry, MemoryScope
from vad.memory.redaction import Redactor
from vad.memory.stores.local import LocalMemoryStore

class MemoryGateway:
    def __init__(self, store: LocalMemoryStore, redactor: Redactor):
        self.store = store
        self.redactor = redactor

    def store_memory(self, entry: MemoryEntry):
        # Apply redaction
        entry.content = self.redactor.redact(entry.content)
        self.store.save(entry)

    def get_memory(self, entry_id: str, scope: MemoryScope) -> Optional[MemoryEntry]:
        return self.store.retrieve(entry_id, scope)

    def get_by_scope(self, scope: MemoryScope) -> list[MemoryEntry]:
        return self.store.get_by_scope(scope)
