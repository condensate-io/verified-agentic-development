import hashlib
import json
from pydantic import BaseModel
from pydantic import field_validator
from typing import List, Optional
from enum import Enum

from vad.contracts.models import EIP
from vad.contracts.normalize import normalize_eip

class VerifyStatus(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    PENDING = "pending"
    UNMAPPED = "unmapped"

class ProofMapping(BaseModel):
    obligation_id: str
    test_command: str

class ProofPlan(BaseModel):
    schema_version: str = "1.0.0"
    eip_version: str
    eip_digest: str
    mappings: List[ProofMapping]
    required_manual_gates: List[str] = []

    @field_validator("mappings")
    @classmethod
    def reject_duplicate_mappings(cls, mappings: List[ProofMapping]) -> List[ProofMapping]:
        obligation_ids = [mapping.obligation_id for mapping in mappings]
        if len(obligation_ids) != len(set(obligation_ids)):
            raise ValueError("ProofPlan contains duplicate obligation mappings.")
        return mappings

    def get_mapping(self, obligation_id: str) -> Optional[ProofMapping]:
        for m in self.mappings:
            if m.obligation_id == obligation_id:
                return m
        return None

    def covers_obligation(self, obligation_id: str) -> bool:
        return self.get_mapping(obligation_id) is not None or obligation_id in self.required_manual_gates


def compute_eip_digest(eip: EIP) -> str:
    payload = json.dumps(normalize_eip(eip), sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
