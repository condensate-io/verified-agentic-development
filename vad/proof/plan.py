from pydantic import BaseModel
from typing import List, Optional
from enum import Enum

class VerifyStatus(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    PENDING = "pending"
    UNMAPPED = "unmapped"

class ProofMapping(BaseModel):
    obligation_id: str
    test_command: str

class ProofPlan(BaseModel):
    eip_version: str
    mappings: List[ProofMapping]

    def get_mapping(self, obligation_id: str) -> Optional[ProofMapping]:
        for m in self.mappings:
            if m.obligation_id == obligation_id:
                return m
        return None
