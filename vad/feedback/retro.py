import json
import uuid
from typing import Dict, Any

from vad.evidence.bundle import EvidenceBundle
from vad.feedback.proposals import FeedbackProposal, ProposalType
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
        proposals = _proposals_from_failures(failures)
        
        entry = MemoryEntry(
            id=str(uuid.uuid4()),
            scope=MemoryScope.RETROSPECTIVE,
            content=json.dumps({
                "failures": failures,
                "learning": learning_summary,
                "proposals": [proposal.model_dump(mode="json") for proposal in proposals],
            })
        )
        self.memory_gateway.store_memory(entry)
        
        return {
            "failures": failures,
            "learning": learning_summary,
            "proposals": [proposal.model_dump(mode="json") for proposal in proposals],
            "entry_id": entry.id
        }


def _proposals_from_failures(failures: list[str]) -> list[FeedbackProposal]:
    proposals = []
    lowered = [failure.lower() for failure in failures]

    if _has_repeated_failure(lowered):
        proposals.append(FeedbackProposal(
            proposal_type=ProposalType.ADD_PROOF_OBLIGATION,
            reason="Repeated failure requires executable proof coverage.",
            payload={"kind": "unit", "description": "Cover repeated failure mode."},
        ))
        proposals.append(FeedbackProposal(
            proposal_type=ProposalType.ADD_INVARIANT,
            reason="Repeated failure should become an explicit invariant.",
            payload={"category": "functional", "description": "Repeated failure mode must not recur."},
        ))

    if any("policy" in failure or "denial" in failure or "denied" in failure for failure in lowered):
        proposals.append(FeedbackProposal(
            proposal_type=ProposalType.RESTRICT_TOOL,
            reason="Policy denial indicates the allowed tool surface should be tightened.",
            payload={"restriction": "review denied capability before reuse"},
        ))

    if any("loop" in failure or "exhaust" in failure or "budget" in failure for failure in lowered):
        proposals.append(FeedbackProposal(
            proposal_type=ProposalType.ADJUST_BUDGET,
            reason="Loop or budget exhaustion requires revised execution bounds.",
            payload={"budget": "review loop depth and token budget"},
        ))

    return proposals


def _has_repeated_failure(failures: list[str]) -> bool:
    normalized = [failure.strip() for failure in failures if failure.strip()]
    return len(normalized) != len(set(normalized))
