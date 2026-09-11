"""Typed decision catalog for enterprise decision registry and audit traceability."""

from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional
import yaml
from pydantic import BaseModel, Field


class DecisionCategory(str, Enum):
    ADMISSION = "admission"
    TOOL_EXECUTION = "tool_execution"
    HARNESS_REVIEW = "harness_review"
    RELEASE_GATE = "release_gate"
    POLICY_OVERRIDE = "policy_override"
    INCIDENT_CLOSURE = "incident_closure"


class DecisionOutcome(BaseModel):
    name: str
    description: str
    requires_human: bool = False
    escalation_tier: Optional[str] = None


class DecisionEntry(BaseModel):
    id: str
    category: str
    title: str
    description: str
    outcomes: List[DecisionOutcome] = Field(default_factory=list)
    rationale_template: str
    required_evidence_fields: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class DecisionCatalog:
    """In-memory and file-backed catalog of standardized decision types."""

    def __init__(self, entries: Optional[List[DecisionEntry]] = None):
        self._entries: Dict[str, DecisionEntry] = {}
        if entries:
            for entry in entries:
                self.register(entry)

    def register(self, entry: DecisionEntry) -> None:
        self._entries[entry.id] = entry

    def get(self, decision_id: str) -> Optional[DecisionEntry]:
        return self._entries.get(decision_id)

    def list_all(self) -> List[DecisionEntry]:
        return list(self._entries.values())

    def list_by_category(self, category: str) -> List[DecisionEntry]:
        return [e for e in self._entries.values() if e.category == category]

    def to_yaml(self) -> str:
        data = [entry.model_dump() for entry in self._entries.values()]
        return yaml.dump({"decisions": data}, sort_keys=False)

    @classmethod
    def load_yaml(cls, yaml_content: str) -> "DecisionCatalog":
        data = yaml.safe_load(yaml_content) or {}
        entries_data = data.get("decisions", [])
        entries = [DecisionEntry(**item) for item in entries_data]
        return cls(entries)

    @classmethod
    def load_file(cls, path: str) -> "DecisionCatalog":
        p = Path(path)
        if not p.exists():
            return cls()
        with open(p, "r", encoding="utf-8") as f:
            return cls.load_yaml(f.read())

    def save_file(self, path: str) -> None:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(self.to_yaml())

    @classmethod
    def default_catalog(cls) -> "DecisionCatalog":
        catalog = cls()
        catalog.register(
            DecisionEntry(
                id="dec-dor-admit",
                category=DecisionCategory.ADMISSION.value,
                title="Definition of Ready Task Admission",
                description="Decides if an EIP can be admitted for agentic execution.",
                outcomes=[
                    DecisionOutcome(name="admit", description="Admit to execution dep-graph"),
                    DecisionOutcome(name="reject", description="Reject EIP back to intent steward"),
                    DecisionOutcome(name="defer", description="Request clarifications"),
                ],
                rationale_template="EIP {eip_name} evaluation resulted in status {status} with findings: {findings}",
                required_evidence_fields=["eip_digest", "evaluation_result", "evaluated_by"],
            )
        )
        catalog.register(
            DecisionEntry(
                id="dec-release-gate",
                category=DecisionCategory.RELEASE_GATE.value,
                title="Production Release Gate Decision",
                description="Evaluates whether release gates and proofs allow promotion.",
                outcomes=[
                    DecisionOutcome(name="promote", description="Promote release candidate"),
                    DecisionOutcome(name="hold", description="Hold for telemetry analysis", requires_human=True),
                    DecisionOutcome(name="rollback", description="Trigger rollback"),
                ],
                rationale_template="Release for {eip_name} decision: {outcome}. Attestation hash: {attestation_hash}",
                required_evidence_fields=["attestation_hash", "dsse_envelope", "telemetry_metrics"],
            )
        )
        return catalog
