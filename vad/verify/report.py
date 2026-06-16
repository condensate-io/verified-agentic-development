from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime
from vad.proof.plan import VerifyStatus

class VerifierResult(BaseModel):
    obligation_id: str
    status: VerifyStatus
    output: str
    error: Optional[str] = None

class VerifierReport(BaseModel):
    timestamp: datetime
    results: List[VerifierResult]
    passed: bool

    def to_json(self) -> str:
        return self.model_dump_json(indent=2)
