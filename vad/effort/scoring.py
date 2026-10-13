from enum import Enum
import subprocess
from pydantic import BaseModel, Field, model_validator


class EffortType(str, Enum):
    BUGFIX = "bugfix"
    FEATURE = "feature"
    REFACTOR = "refactor"
    GREENFIELD = "greenfield"
    TEST = "test"
    MIGRATION = "migration"


class MeesPenalties(BaseModel):
    complexity: int = Field(default=0, ge=0)
    maintainability: int = Field(default=0, ge=0)
    diff: int = Field(default=0, ge=0)
    spread: int = Field(default=0, ge=0)
    dependency: int = Field(default=0, ge=0)
    novelty: int = Field(default=0, ge=0)
    risk: int = Field(default=0, ge=0)

    @property
    def total(self) -> int:
        return (
            self.complexity
            + self.maintainability
            + self.diff
            + self.spread
            + self.dependency
            + self.novelty
            + self.risk
        )


class EffortScore(BaseModel):
    effort_type: EffortType
    score: int = Field(ge=0, le=100)
    policy: str
    penalties: MeesPenalties
    warnings: list[str] = Field(default_factory=list)
    requires_human: bool = False

    @model_validator(mode="after")
    def align_policy(self):
        expected = policy_for_score(self.score)
        if self.policy != expected:
            raise ValueError(f"policy must be {expected} for score {self.score}")
        if self.policy == "block" and not self.requires_human:
            raise ValueError("blocking MEES policy requires human review")
        return self


class DiffMetrics(BaseModel):
    changed_files: int = Field(ge=0)
    insertions: int = Field(ge=0)
    deletions: int = Field(ge=0)
    dependency_files_changed: int = Field(ge=0)
    warnings: list[str] = Field(default_factory=list)

    @property
    def line_delta(self) -> int:
        return self.insertions + self.deletions


class QualityMetrics(BaseModel):
    cyclomatic_delta: int | None = None
    cognitive_delta: int | None = None
    maintainability_delta: int | None = None
    warnings: list[str] = Field(default_factory=list)


def score_effort(
    effort_type: EffortType | str,
    penalties: MeesPenalties | None = None,
    metric_warnings: list[str] | None = None,
) -> EffortScore:
    resolved_type = EffortType(effort_type)
    resolved_penalties = penalties or MeesPenalties()
    score = max(0, 100 - resolved_penalties.total)
    policy = policy_for_score(score)
    warnings = list(metric_warnings or [])
    if penalties is None:
        warnings.append("MEES scored from explicit zero-penalty fallback; no metric tool output supplied")
    return EffortScore(
        effort_type=resolved_type,
        score=score,
        policy=policy,
        penalties=resolved_penalties,
        warnings=warnings,
        requires_human=policy == "block",
    )


def collect_git_diff_metrics(
    before: str = "HEAD",
    after: str = "WORKTREE",
    cwd: str | None = None,
    runner=None,
) -> DiffMetrics:
    runner = runner or subprocess.run
    diff_range = [before] if after == "WORKTREE" else [before, after]
    try:
        numstat = runner(
            ["git", "diff", "--numstat", *diff_range],
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
        )
        status = runner(
            ["git", "diff", "--name-only", *diff_range],
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        return DiffMetrics(changed_files=0, insertions=0, deletions=0, dependency_files_changed=0, warnings=["git unavailable"])

    warnings = []
    if numstat.returncode != 0 or status.returncode != 0:
        return DiffMetrics(
            changed_files=0,
            insertions=0,
            deletions=0,
            dependency_files_changed=0,
            warnings=["git diff metrics unavailable"],
        )

    insertions, deletions = _parse_numstat(numstat.stdout)
    changed_files = [line.strip() for line in status.stdout.splitlines() if line.strip()]
    dependency_files = [path for path in changed_files if _is_dependency_manifest(path)]
    return DiffMetrics(
        changed_files=len(changed_files),
        insertions=insertions,
        deletions=deletions,
        dependency_files_changed=len(dependency_files),
        warnings=warnings,
    )


def penalties_from_diff_metrics(metrics: DiffMetrics) -> MeesPenalties:
    return MeesPenalties(
        diff=metrics.line_delta,
        spread=metrics.changed_files,
        dependency=metrics.dependency_files_changed * 10,
    )


def quality_metrics_from_supplied(
    cyclomatic_delta: int | None = None,
    cognitive_delta: int | None = None,
    maintainability_delta: int | None = None,
) -> QualityMetrics:
    warnings = []
    if cyclomatic_delta is None:
        warnings.append("cyclomatic complexity metric unavailable")
    if cognitive_delta is None:
        warnings.append("cognitive complexity metric unavailable")
    if maintainability_delta is None:
        warnings.append("maintainability metric unavailable")
    return QualityMetrics(
        cyclomatic_delta=cyclomatic_delta,
        cognitive_delta=cognitive_delta,
        maintainability_delta=maintainability_delta,
        warnings=warnings,
    )


def penalties_from_quality_metrics(metrics: QualityMetrics) -> MeesPenalties:
    complexity_penalty = max(0, metrics.cyclomatic_delta or 0) + max(0, metrics.cognitive_delta or 0)
    maintainability_penalty = max(0, -(metrics.maintainability_delta or 0))
    return MeesPenalties(complexity=complexity_penalty, maintainability=maintainability_penalty)


def policy_for_score(score: int) -> str:
    if score < 50:
        return "block"
    if score < 70:
        return "warn"
    return "pass"


def review_depth_for_mees(score: int) -> str:
    """Determines review depth tier from MEES:
    - >= 70: solo review
    - 50-69: adds a domain agent
    - < 50: adds a domain agent plus the adversarial verifier
    """
    if score >= 70:
        return "solo"
    elif score >= 50:
        return "domain"
    else:
        return "adversarial"


def required_reviewers_for_mees(score: int) -> list[str]:
    """Returns required reviewer agent types based on MEES score."""
    if score >= 70:
        return ["solo"]
    elif score >= 50:
        return ["solo", "domain"]
    else:
        return ["solo", "domain", "adversarial"]


def _parse_numstat(output: str) -> tuple[int, int]:
    insertions = 0
    deletions = 0
    for line in output.splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        added, removed = parts[0], parts[1]
        if added.isdigit():
            insertions += int(added)
        if removed.isdigit():
            deletions += int(removed)
    return insertions, deletions


def _is_dependency_manifest(path: str) -> bool:
    name = path.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    return name in {
        "package.json",
        "package-lock.json",
        "pnpm-lock.yaml",
        "yarn.lock",
        "requirements.txt",
        "pyproject.toml",
        "poetry.lock",
        "Cargo.toml",
        "Cargo.lock",
        "go.mod",
        "go.sum",
    }


class MeesBudget(BaseModel):
    max_changed_files: int = 10
    max_complexity_increase: int = 5
    allow_new_dependencies: bool = False


class MeesEvaluationResult(BaseModel):
    allow: bool
    warn: bool
    deny_reasons: list[str] = Field(default_factory=list)


def evaluate_mees_policy(input_data: dict) -> MeesEvaluationResult:
    """Evaluates input data against vad.minimal_effort Rego policy logic.
    Matches rules declared in policies/mees.rego:
    - Pass: mees >= 70, changed_files <= budget.max_changed_files, complexity_delta <= budget.max_complexity_increase
    - Warn: 50 <= mees < 70 OR changed_files in (budget.max_changed_files, budget.max_changed_files + 3]
    - Deny: mees < 50, changed_files > budget.max_changed_files + 3, complexity_delta > budget + 2, new_deps > 0
    """
    mees = input_data.get("mees", 0)
    changed_files = input_data.get("changed_files", 0)
    complexity_delta = input_data.get("complexity_delta", 0)
    new_deps = input_data.get("new_dependencies", 0)

    budget_raw = input_data.get("budget", {})
    if isinstance(budget_raw, dict):
        budget = MeesBudget(**budget_raw)
    elif isinstance(budget_raw, MeesBudget):
        budget = budget_raw
    else:
        budget = MeesBudget()

    # Pass: MEES >= 70 and within budget
    allow = (
        mees >= 70
        and changed_files <= budget.max_changed_files
        and complexity_delta <= budget.max_complexity_increase
    )

    # Warn: MEES 50-69 or slightly over budget
    warn = (
        (50 <= mees < 70)
        or (budget.max_changed_files < changed_files <= budget.max_changed_files + 3)
    )

    # Deny reasons
    deny_reasons: list[str] = []
    if mees < 50:
        deny_reasons.append(f"MEES score {mees} is below minimum threshold (50)")

    if changed_files > budget.max_changed_files + 3:
        deny_reasons.append(
            f"Changed {changed_files} files exceeds budget ({budget.max_changed_files}) + tolerance (3)"
        )

    if complexity_delta > budget.max_complexity_increase + 2:
        deny_reasons.append(
            f"Complexity increase {complexity_delta} exceeds budget ({budget.max_complexity_increase}) + tolerance (2)"
        )

    if new_deps > 0 and not budget.allow_new_dependencies:
        deny_reasons.append(
            f"Added {new_deps} new dependencies but budget disallows new deps"
        )

    return MeesEvaluationResult(
        allow=allow,
        warn=warn,
        deny_reasons=deny_reasons,
    )

