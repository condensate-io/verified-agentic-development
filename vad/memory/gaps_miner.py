"""Gaps Miner: Analyzes skill execution history to surface recurring failure modes and improvement recommendations."""

from collections import Counter
from typing import Dict, List, Optional
from pydantic import BaseModel, Field
from vad.memory.skill_ledger import SkillLedger, SkillOutcome


class SkillGap(BaseModel):
    skill_name: str
    failure_count: int
    total_runs: int
    failure_rate: float
    recurring_patterns: List[str] = Field(default_factory=list)
    recommendations: List[str] = Field(default_factory=list)


class SkillGapsReport(BaseModel):
    total_entries_analyzed: int
    gaps: List[SkillGap] = Field(default_factory=list)
    priority_skills_to_improve: List[str] = Field(default_factory=list)


class GapsMiner:
    """Mines the skill ledger for systemic gaps, brittle skills, and recurrent error patterns."""

    def __init__(
        self,
        ledger: Optional[SkillLedger] = None,
        failure_rate_threshold: float = 0.25,
        min_samples: int = 2,
    ):
        self.ledger = ledger or SkillLedger()
        self.failure_rate_threshold = failure_rate_threshold
        self.min_samples = min_samples

    def mine_gaps(self) -> SkillGapsReport:
        all_entries = self.ledger.get_entries(limit=1000)
        by_skill: Dict[str, List] = {}
        for e in all_entries:
            by_skill.setdefault(e.skill_name, []).append(e)

        gaps = []
        for skill_name, entries in by_skill.items():
            if len(entries) < self.min_samples:
                continue

            failures = [e for e in entries if e.outcome == SkillOutcome.FAILURE]
            failure_rate = len(failures) / len(entries)

            if failure_rate >= self.failure_rate_threshold and failures:
                # Find recurring error patterns
                error_words = []
                for f in failures:
                    if f.error_snippet:
                        error_words.extend(f.error_snippet.lower().split())

                common_tokens = [w for w, count in Counter(error_words).most_common(3) if len(w) > 3]
                patterns = [f"Recurrent error involving '{token}'" for token in common_tokens]

                recommendations = [
                    f"Refine instructions in SKILL.md for '{skill_name}' to handle edge cases",
                    f"Add explicit validation before invoking external tooling in '{skill_name}'",
                ]

                gaps.append(
                    SkillGap(
                        skill_name=skill_name,
                        failure_count=len(failures),
                        total_runs=len(entries),
                        failure_rate=round(failure_rate, 4),
                        recurring_patterns=patterns,
                        recommendations=recommendations,
                    )
                )

        gaps.sort(key=lambda g: g.failure_rate, reverse=True)
        priority = [g.skill_name for g in gaps]

        return SkillGapsReport(
            total_entries_analyzed=len(all_entries),
            gaps=gaps,
            priority_skills_to_improve=priority,
        )
