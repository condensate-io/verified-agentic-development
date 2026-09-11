"""Definition of Ready (DoR) automated gate for inbound tickets and work items."""
from __future__ import annotations

import re
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field 

from vad.connectors.models import WorkIntakePayload


class DoREvaluationStatus(str, Enum):
    READY = "ready"
    DOR_DEFICIENT = "dor_deficient"
    REJECTED = "rejected"


class DoRCheckResult(BaseModel):
    name: str
    passed: bool
    message: str


class IntakeDoRResult(BaseModel):
    status: DoREvaluationStatus
    passed: bool
    score: float = Field(ge=0.0, le=1.0)
    checks: List[DoRCheckResult] = Field(default_factory=list)
    missing_criteria: List[str] = Field(default_factory=list)
    feedback_message: str


class IntakeDoRConfig(BaseModel):
    min_title_length: int = 5
    min_description_length: int = 20
    require_acceptance_criteria: bool = True
    require_target_repo: bool = False
    reject_prohibited_patterns: bool = True
    prohibited_patterns: List[str] = Field(default_factory=lambda: [
        r"ignore\s+(all\s+)?previous\s+instructions",
        r"disregard\s+(all\s+)?prior\s+prompts",
        r"system\s*:\s*you\s+are\s+now",
        r"rm\s+-rf\s+[/~]",
    ])


class IntakeDoRPolicy:
    '''Evaluates inbound work intake against automated Definition of Ready criteria.'''

    def __init__(self, config: Optional[IntakeDoRConfig] = None):
        self.config = config or IntakeDoRConfig()

    def evaluate(self, payload: WorkIntakePayload | Dict[str, Any]) -> IntakeDoRResult:
        if isinstance(payload, dict):
            title = str(payload.get('title', '')).strip()
            description = str(payload.get('description', '')).strip()
            criteria = payload.get('acceptance_criteria', []) or []
            target_repo = payload.get('target_repo')
        else:
            title = payload.title.strip()
            description = payload.description.strip()
            criteria = payload.acceptance_criteria or []
            target_repo = payload.target_repo

        checks: List[DoRCheckResult] = []
        missing: List[str] = []

        # 1. Prohibited / Safety check
        in_safe = True
        violation = None
        if self.config.reject_prohibited_patterns:
            combined = f"{title}\n{description}"
            for pat in self.config.prohibited_patterns:
                if re.search(pat, combined, flags=re.IGNORECASE):
                    in_safe = False
                    violation = pat
                    break

        checks.append(DoRCheckResult(
            name='safety_content_guard',
            passed=in_safe,
            message='Content passed safety guard' if in_safe else f'Prohibited pattern detected: {violation}'
        ))
        if not in_safe:
            return IntakeDoRResult(
                status=DoREvaluationStatus.REJECTED,
                passed=False,
                score=0.0,
                checks=checks,
                missing_criteria=['safety_violation'],
                feedback_message='Ticket rejected by automated safety policy: prohibited instruction detected.',
            )

        # 2. Title check
        title_passed = len(title) >= self.config.min_title_length
        checks.append(DoRCheckResult(
            name='title_length',
            passed=title_passed,
            message=f'Title length ({len(title)}) meets minimum ({self.config.min_title_length})' if title_passed else f'Title is too short (< {self.config.min_title_length} chars)'
        ))
        if not title_passed:
            missing.append('Descriptive title of at least 5 characters')

        # 3. Description check
        desc_passed = len(description) >= self.config.min_description_length
        checks.append(DoRCheckResult(
            name='description_length',
            passed=desc_passed,
            message=f'Description length ({len(description)}) meets minimum ({self.config.min_description_length})' if desc_passed else f'Description is too short or missing (< {self.config.min_description_length} chars)'
        ))
        if not desc_passed:
            missing.append('Detailed problem description (at least 20 characters)')

        # 4. Acceptance criteria check
        ac_passed = True
        if self.config.require_acceptance_criteria:
            has_explicit = bool(criteria and len(criteria) > 0)
            has_markdown_ac = bool(
                re.search(r"(?i)(acceptance criteria|expected result|expected behavior|definition of done|steps to reproduce)", description)
                or re.search(r"(?m)^\s*[-*]\s*\[[\sxX]\]", description)
            )
            ac_passed = has_explicit or has_markdown_ac
            checks.append(DoRCheckResult(
                name='acceptance_criteria',
                passed=ac_passed,
                message='Acceptance criteria present' if ac_passed else 'No acceptance criteria or verifiable goals identified'
            ))
            if not ac_passed:
                missing.append('Clear acceptance criteria or verifiable expected results')

        # 5. Target repo check (if required)
        repo_passed = True
        if self.config.require_target_repo:
            repo_passed = bool(target_repo and str(target_repo).strip())
            checks.append(DoRCheckResult(
                name='target_repository',
                passed=repo_passed,
                message='Target repository specified' if repo_passed else 'Missing target repository context'
            ))
            if not repo_passed:
                missing.append('Target repository URI')

        total_checks = len(checks)
        passed_checks = sum(1 for c in checks if c.passed)
        score = round(passed_checks / total_checks, 2) if total_checks > 0 else 1.0
        all_passed = (passed_checks == total_checks)

        if all_passed:
            status = DoREvaluationStatus.READY
            feedback = f'Definition of Ready (DoR) gate passed (score: {score:.2f}). Ready for governed agent intake.'
        else:
            status = DoREvaluationStatus.DOR_DEFICIENT
            feedback_lines = [
                'Definition of Ready (DoR) evaluation failed before agent assignment:',
                *[f'- {item}' for item in missing],
                'Please update the ticket with the missing details to satisfy the DoR gate.'
            ]
            feedback = '\n'.join(feedback_lines)

        return IntakeDoRResult(
            status=status,
            passed=all_passed,
            score=score,
            checks=checks,
            missing_criteria=missing,
            feedback_message=feedback,
        )


def evaluate_intake_dor(payload: WorkIntakePayload | Dict[str, Any], config: Optional[IntakeDoRConfig] = None) -> IntakeDoRResult:
    return IntakeDoRPolicy(config).evaluate(payload)
