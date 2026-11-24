import json
import uuid
from enum import Enum
from typing import Dict, Any

from vad.evidence.bundle import EvidenceBundle
from vad.feedback.proposals import FeedbackProposal, ProposalType
from vad.feedback.publisher import LearningLoopPublisher
from vad.memory.gateway import MemoryGateway
from vad.memory.contracts import (
    FactType,
    InferredLesson,
    MemoryEntry,
    MemoryScope,
    ProposedRuleType,
)
from vad.server.db.store import ServerStore

_PROPOSAL_TO_RULE_TYPE: dict[ProposalType, ProposedRuleType] = {
    ProposalType.ADD_PROOF_OBLIGATION: ProposedRuleType.PROOF_OBLIGATION,
    ProposalType.ADD_INVARIANT: ProposedRuleType.INVARIANT,
    ProposalType.STRENGTHEN_INVARIANT: ProposedRuleType.INVARIANT,
    ProposalType.RESTRICT_TOOL: ProposedRuleType.TOOL_RESTRICTION,
    ProposalType.REDUCE_AUTONOMY: ProposedRuleType.TOOL_RESTRICTION,
    ProposalType.ADJUST_BUDGET: ProposedRuleType.RISK_CALIBRATION,
    ProposalType.CHANGE_MODEL_TIER: ProposedRuleType.RISK_CALIBRATION,
    ProposalType.ADD_RELEASE_GATE: ProposedRuleType.RISK_CALIBRATION,
}


class RetroAnalyzer:
    def __init__(self, memory_gateway: MemoryGateway):
        self.memory_gateway = memory_gateway

    def analyze(self, bundle: EvidenceBundle) -> Dict[str, Any]:
        """
        Extract failures from the EvidenceBundle and synthesize learnings.
        Writes learning back into memory store under RETROSPECTIVE scope.
        """
        failures = _extract_failures(bundle.data)

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

    def analyze_for_learning_loop(
        self,
        bundle: EvidenceBundle,
        *,
        run_id: str,
        eip_digest: str,
        risk_tier: str,
        domain_tags: list[str] | None = None,
        store: ServerStore | None = None,
    ) -> dict[str, Any]:
        """Extract an ObservedFact and unapproved PROPOSED InferredLessons for the learning loop."""
        data = bundle.data
        failures = _extract_failures(data)
        if not failures and data.get("blocker"):
            failures.append(str(data["blocker"]))

        evidence_digest = bundle.compute_hash()
        fact_type = _classify_fact_type(data, failures)
        risk_tier_value = risk_tier.value if isinstance(risk_tier, Enum) else str(risk_tier)

        if failures:
            summary = f"Run {run_id} observed {len(failures)} failure(s): {'; '.join(str(f) for f in failures)}"
        else:
            summary = f"Run {run_id} recorded outcome {fact_type.value} (evidence {evidence_digest[:12]})"

        publisher = LearningLoopPublisher(memory_gateway=self.memory_gateway, store=store)
        fact = publisher.publish_run_outcome(
            run_id=run_id,
            eip_digest=eip_digest,
            evidence_digest=evidence_digest,
            risk_tier=risk_tier_value,
            fact_type=fact_type,
            summary=summary,
            domain_tags=domain_tags,
            metadata={
                "failure_count": len(failures),
                "failures": list(failures),
            },
        )

        lessons: list[InferredLesson] = []
        proposals = _proposals_from_failures(failures)
        for proposal in proposals:
            rule_type = _PROPOSAL_TO_RULE_TYPE.get(
                proposal.proposal_type, ProposedRuleType.GENERAL_GUIDANCE
            )
            description = str(
                proposal.payload.get("description")
                or proposal.payload.get("restriction")
                or proposal.payload.get("budget")
                or proposal.reason
            )
            lessons.append(
                publisher.publish_candidate_lesson(
                    title=f"Candidate {rule_type.value.replace('_', ' ')}: {proposal.proposal_type.value}",
                    recommendation=f"{proposal.reason} Suggested action: {description}",
                    proposed_rule_type=rule_type,
                    derived_from_fact_ids=[fact.fact_id],
                    domain_tags=domain_tags,
                    confidence=0.65,
                )
            )

        if failures and not lessons:
            lessons.append(
                publisher.publish_candidate_lesson(
                    title=f"Candidate guidance for failure in {run_id}",
                    recommendation=f"Investigate and add regression coverage for: {failures[0]}",
                    proposed_rule_type=ProposedRuleType.GENERAL_GUIDANCE,
                    derived_from_fact_ids=[fact.fact_id],
                    domain_tags=domain_tags,
                    confidence=0.5,
                )
            )

        return {
            "run_id": run_id,
            "evidence_digest": evidence_digest,
            "failures": failures,
            "observed_fact": fact,
            "candidate_lessons": lessons,
        }


def _extract_failures(data: dict[str, Any]) -> list[str]:
    failures: list[str] = []
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
    return failures


def _classify_fact_type(data: dict[str, Any], failures: list[str]) -> FactType:
    final_decision = str(data.get("final_decision", "")).strip().lower()
    lowered_failures = [str(failure).lower() for failure in failures]

    if (
        any("policy" in f or "denial" in f or "denied" in f for f in lowered_failures)
        or final_decision == "denied"
    ):
        return FactType.POLICY_DENIAL
    if final_decision == "rejected":
        return FactType.REJECTED_CHANGE
    verification = data.get("verification")
    verification_failed = isinstance(verification, dict) and verification.get("passed") is False
    if failures or verification_failed or final_decision in {"failed", "blocked", "needs_human"}:
        return FactType.VERIFICATION_FAILURE
    return FactType.VERIFIED_DELIVERY


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
