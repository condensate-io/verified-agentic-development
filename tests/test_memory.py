import pytest
from vad.memory.contracts import MemoryEntry, MemoryScope
from vad.memory.redaction import Redactor
from vad.memory.stores.local import LocalMemoryStore
from vad.memory.gateway import MemoryGateway

def test_memory_payload_size_limit():
    store = LocalMemoryStore()
    scope = MemoryScope(owner="agent-1", purpose="task-a", max_payload_size=10)
    entry = MemoryEntry(id="1", scope=scope, content="This is too large")
    
    with pytest.raises(ValueError, match="Payload size exceeds scope limit"):
        store.save(entry)

def test_memory_scope_restrictions():
    store = LocalMemoryStore()
    scope1 = MemoryScope(owner="agent-1", purpose="task-a")
    scope2 = MemoryScope(owner="agent-2", purpose="task-b")
    
    entry = MemoryEntry(id="1", scope=scope1, content="Secret agent-1 data")
    store.save(entry)
    
    # Successful retrieve
    retrieved = store.retrieve("1", scope1)
    assert retrieved is not None
    assert retrieved.content == "Secret agent-1 data"
    
    # Scope mismatch
    with pytest.raises(PermissionError, match="Scope mismatch"):
        store.retrieve("1", scope2)

def test_memory_redaction_coverage():
    store = LocalMemoryStore()
    redactor = Redactor()
    gateway = MemoryGateway(store, redactor)
    
    scope = MemoryScope(owner="agent-1", purpose="general")
    entry = MemoryEntry(id="1", scope=scope, content="My email is test@example.com and card is 1234-5678-9012-3456.")
    
    gateway.store_memory(entry)
    
    retrieved = gateway.get_memory("1", scope)
    assert "test@example.com" not in retrieved.content
    assert "1234-5678-9012-3456" not in retrieved.content
    assert "[REDACTED]" in retrieved.content

def test_local_memory_store_persists_across_instances(tmp_path):
    memory_file = tmp_path / "memory.json"
    scope = MemoryScope.RETROSPECTIVE
    entry = MemoryEntry(id="1", scope=scope, content="Durable learning")

    LocalMemoryStore(file_path=str(memory_file)).save(entry)
    restored = LocalMemoryStore(file_path=str(memory_file))

    assert restored.retrieve("1", scope).content == "Durable learning"
