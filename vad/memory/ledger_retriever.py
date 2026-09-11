"""Ledger Retriever: Injects historical skill execution context and failure caveats into SessionStart hooks."""

from typing import Dict, List, Optional
from pydantic import BaseModel, Field
from vad.memory.skill_ledger import SkillLedger, SkillOutcome


class LedgerContextSnippet(BaseModel):
    skill_name: str
    failure_count: int
    total_runs: int
    common_errors: List[str] = Field(default_factory=list)


class LedgerRetriever:
    """Extracts historical failure memory and recommendations from the skill ledger to enrich prompt context."""

    def __init__(self, ledger: Optional[SkillLedger] = None):
        self.ledger = ledger or SkillLedger()

    def get_context_summary(self, repo: str) -> List[LedgerContextSnippet]:
        entries = self.ledger.get_entries(repo=repo, limit=200)
        by_skill: Dict[str, List] = {}
        for e in entries:
            by_skill.setdefault(e.skill_name, []).append(e)

        snippets = []
        for skill_name, skill_entries in by_skill.items():
            failures = [e for e in skill_entries if e.outcome == SkillOutcome.FAILURE]
            if not failures:
                continue

            error_snippets = list({f.error_snippet for f in failures if f.error_snippet})[:3]
            snippets.append(
                LedgerContextSnippet(
                    skill_name=skill_name,
                    failure_count=len(failures),
                    total_runs=len(skill_entries),
                    common_errors=error_snippets,
                )
            )
        return snippets

    def format_for_session_start(self, repo: str) -> str:
        """Formats historical failure memory for prompt injection during SessionStart."""
        snippets = self.get_context_summary(repo)
        if not snippets:
            return ""

        lines = [
            "### Historical Skill Execution Memory & Known Caveats",
            f"Repository context: `{repo}`. Avoid previously encountered runtime pitfalls:",
        ]
        for s in snippets:
            err_str = "; ".join(s.common_errors) if s.common_errors else "Unspecified failure"
            lines.append(
                f"- **Skill `{s.skill_name}`**: ({s.failure_count}/{s.total_runs} failures). Caution: {err_str}"
            )

        return "\n".join(lines)
