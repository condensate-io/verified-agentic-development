from pydantic import BaseModel, Field
from typing import Optional, Dict, Any, ClassVar

class MemoryScope(BaseModel):
    owner: str
    purpose: str
    max_payload_size: int = Field(default=1024)

    RETROSPECTIVE: ClassVar['MemoryScope']

class MemoryEntry(BaseModel):
    id: str
    scope: MemoryScope
    content: str
    metadata: Dict[str, Any] = Field(default_factory=dict)

MemoryScope.RETROSPECTIVE = MemoryScope(owner="system", purpose="retrospective", max_payload_size=8192)
