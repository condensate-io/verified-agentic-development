"""Review agent calibration engine: evaluates candidate decisions against standardized principles and exemplars."""

from pathlib import Path
from typing import Any, Dict, List, Optional
import yaml
from pydantic import BaseModel, Field


class CalibrationExemplar(BaseModel):
    id: str
    decision_context: str
    ground_truth_outcome: str
    rationale: str
    principles_invoked: List[str] = Field(default_factory=list)
    weight: float = 1.0


class CalibrationReport(BaseModel):
    candidate_decision: str
    candidate_outcome: str
    calibration_score: float  # 0.0 - 1.0
    alignment_delta: float
    is_calibrated: bool
    exemplar_evaluations: List[Dict[str, Any]] = Field(default_factory=list)
    recommendations: List[str] = Field(default_factory=list)


class ReviewAgentCalibrator:
    """Calibrates review agents and decisions against curated enterprise principles and exemplars."""

    def __init__(
        self,
        principles: Optional[List[str]] = None,
        exemplars: Optional[List[CalibrationExemplar]] = None,
        calibration_threshold: float = 0.75,
    ):
        self.principles = principles or [
            "Intent is primary: specifications must be executable and unambiguous",
            "Proof is mandatory: changes require automated verification evidence",
            "Separation of duties: builders must never approve their own code or gates",
            "Continuous governance: audit logs are generated during execution, not retroactively",
            "Minimal effort efficiency: favor simpler, high-maintainability changes",
        ]
        self.exemplars: List[CalibrationExemplar] = exemplars or []
        self.calibration_threshold = calibration_threshold

    def add_exemplar(self, exemplar: CalibrationExemplar) -> None:
        self.exemplars.append(exemplar)

    def load_from_yaml(self, yaml_content: str) -> None:
        data = yaml.safe_load(yaml_content) or {}
        if "principles" in data:
            self.principles = data["principles"]
        for item in data.get("exemplars", []):
            self.exemplars.append(CalibrationExemplar(**item))

    def load_directory(self, dir_path: str) -> None:
        p = Path(dir_path)
        if not p.exists():
            return
        for f in p.glob("*.yaml"):
            with open(f, "r", encoding="utf-8") as file:
                self.load_from_yaml(file.read())

    def calibrate(
        self, decision_context: str, candidate_outcome: str, candidate_rationale: str
    ) -> CalibrationReport:
        if not self.exemplars:
            # Default calibrated when no exemplars are configured
            return CalibrationReport(
                candidate_decision=decision_context,
                candidate_outcome=candidate_outcome,
                calibration_score=1.0,
                alignment_delta=0.0,
                is_calibrated=True,
                recommendations=["No exemplars loaded; calibrated against default principles."],
            )

        evaluations = []
        total_weight = 0.0
        weighted_score = 0.0
        recommendations = []

        # Find exemplars with context overlap
        relevant_exemplars = []
        ctx_tokens = set(decision_context.lower().split())
        for ex in self.exemplars:
            ex_tokens = set(ex.decision_context.lower().split())
            overlap = len(ctx_tokens.intersection(ex_tokens))
            if overlap > 0:
                relevant_exemplars.append((overlap, ex))

        # Sort by relevance
        relevant_exemplars.sort(key=lambda x: x[0], reverse=True)
        active_set = [ex for _, ex in relevant_exemplars[:3]] if relevant_exemplars else self.exemplars[:3]

        for ex in active_set:
            outcome_match = (candidate_outcome.strip().lower() == ex.ground_truth_outcome.strip().lower())
            
            # Check rationale principle alignment
            matched_principles = [p for p in ex.principles_invoked if any(w in candidate_rationale.lower() for w in p.lower().split()[:2])]
            rationale_score = (len(matched_principles) / len(ex.principles_invoked)) if ex.principles_invoked else 1.0
            
            item_score = (0.7 if outcome_match else 0.0) + (0.3 * rationale_score)
            weighted_score += item_score * ex.weight
            total_weight += ex.weight

            evaluations.append({
                "exemplar_id": ex.id,
                "expected": ex.ground_truth_outcome,
                "candidate": candidate_outcome,
                "outcome_match": outcome_match,
                "score": round(item_score, 3),
            })

            if not outcome_match:
                recommendations.append(f"Exemplar {ex.id} expected outcome '{ex.ground_truth_outcome}' because: {ex.rationale}")

        final_score = (weighted_score / total_weight) if total_weight > 0 else 1.0
        delta = 1.0 - final_score
        is_calibrated = final_score >= self.calibration_threshold

        return CalibrationReport(
            candidate_decision=decision_context,
            candidate_outcome=candidate_outcome,
            calibration_score=round(final_score, 3),
            alignment_delta=round(delta, 3),
            is_calibrated=is_calibrated,
            exemplar_evaluations=evaluations,
            recommendations=recommendations,
        )
