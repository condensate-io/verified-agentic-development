from typing import Any, List

from vad.feedback.proposals import FeedbackProposal, ProposalType

class Incident:
    def __init__(self, issue_type: str, severity: str, description: str):
        self.issue_type = issue_type
        self.severity = severity
        self.description = description

class FeedbackRecord:
    def __init__(self, source: str, data: Any):
        self.source = source
        self.data = data

class FeedbackAnalyzer:
    def __init__(self):
        self.incidents: List[Incident] = []
        self.feedback_records: List[FeedbackRecord] = []

    def log_incident(self, incident: Incident):
        self.incidents.append(incident)

    def generate_feedback_record(self, source: str, data: Any) -> FeedbackRecord:
        record = FeedbackRecord(source, data)
        self.feedback_records.append(record)
        return record

    def propose_invariant_updates(self) -> List[str]:
        """
        Proposes invariant updates based on repeated incidents or SLO drift.
        """
        issue_counts = {}
        for inc in self.incidents:
            issue_counts[inc.issue_type] = issue_counts.get(inc.issue_type, 0) + 1

        proposals = []
        for issue_type, count in issue_counts.items():
            if count >= 3:
                proposals.append(f"Proposed Invariant: Ensure system is resilient to '{issue_type}' (seen {count} times).")
        
        # Check feedback records for SLO drift
        for record in self.feedback_records:
            if isinstance(record.data, dict) and record.data.get("slo_drift", False):
                proposals.append("Proposed Invariant: Adjust resource allocation to prevent SLO drift.")

        return proposals

    def propose_release_updates(self, release_outcome: dict[str, Any]) -> list[FeedbackProposal]:
        return proposals_from_release_outcome(release_outcome)


def proposals_from_release_outcome(release_outcome: dict[str, Any]) -> list[FeedbackProposal]:
    if release_outcome.get("decision") != "failed":
        return []

    error = str(release_outcome.get("error", ""))
    lowered = error.lower()

    if "missing telemetry" in lowered:
        return [FeedbackProposal(
            proposal_type=ProposalType.ADD_PROOF_OBLIGATION,
            reason="Release failed because telemetry evidence was missing.",
            payload={
                "kind": "telemetry",
                "description": "Prove release telemetry is integrated before promotion.",
            },
        )]

    if "rollback triggered" in lowered:
        metric = _extract_release_metric(error)
        return [FeedbackProposal(
            proposal_type=ProposalType.ADD_RELEASE_GATE,
            reason="Rollback outcome should become an explicit release gate.",
            payload={
                "metric": metric,
                "description": f"Gate release promotion on {metric} telemetry.",
            },
        )]

    return []


def _extract_release_metric(error: str) -> str:
    parts = error.split("'")
    if len(parts) >= 3 and parts[1].strip():
        return parts[1]
    return "release_health"
