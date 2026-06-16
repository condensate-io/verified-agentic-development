from pydantic import BaseModel
from typing import List, Optional

class AgentCapabilities(BaseModel):
    tools: List[str]
    model_tier: str
    memory_scope: str

class AgentCard(BaseModel):
    name: str
    description: str
    capabilities: AgentCapabilities
    
    # Who created and approved this card
    builder: str
    approver: str
    
    def to_dict(self):
        return self.model_dump()
