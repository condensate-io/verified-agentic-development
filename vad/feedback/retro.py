import json
import uuid
from typing import Dict, Any

from vad.evidence.bundle import EvidenceBundle
from vad.memory.gateway import MemoryGateway
from vad.memory.contracts import MemoryEntry, MemoryScope

class RetroAnalyzer:
    def __init__(self, memory_gateway: MemoryGateway):
        self.memory_gateway = memory_gateway

    def analyze(self, bundle: EvidenceBundle) -> Dict[str, Any]:
        """
        Extract failures from the EvidenceBundle and synthesize learnings.
        Writes learning back into memory store under RETROSPECTIVE scope.
        """
        data = bundle.data
        failures = []
        
        if "failures" in data and isinstance(data["failures"], list):
            failures.extend(data["failures"])
            
        if "steps" in data and isinstance(data["steps"], list):
            for step in data["steps"]:
                if step.get("status") == "failure":
                    if "error" in step:
                        failures.append(step["error"])
                    if "policy_denial" in step:
                        failures.append(step["policy_denial"])
                    if "loop_exhaustion" in step:
                        failures.append(step["loop_exhaustion"])
                        
        # Synthesize learning summary
        learning_summary = f"Analyzed bundle. Extracted {len(failures)} failure modes: {failures}"
        
        entry = MemoryEntry(
            id=str(uuid.uuid4()),
            scope=MemoryScope.RETROSPECTIVE,
            content=json.dumps({
                "failures": failures,
                "learning": learning_summary
            })
        )
        self.memory_gateway.store_memory(entry)
        
        return {
            "failures": failures,
            "learning": learning_summary,
            "entry_id": entry.id
        }
