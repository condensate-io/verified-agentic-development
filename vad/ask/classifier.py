from vad.ask.assessment import AskAssessment, EffortType, MeesBudget
from vad.ask.questions import clarification_questions
from vad.contracts.models import AutonomyTier, RiskTier


def assess_ask(ask: str) -> AskAssessment:
    text = ask.strip()
    lowered = text.lower()
    questions = clarification_questions(text)
    risk = _risk_tier(lowered)
    effort = _effort_type(lowered)
    autonomy = _autonomy_tier(risk, questions, lowered)
    release_needed = any(word in lowered for word in ("deploy", "release", "production", "rollout"))
    telemetry_needed = release_needed or risk == RiskTier.HIGH

    return AskAssessment(
        summary=_summary(text),
        ambiguity_score=min(100, len(questions) * 35),
        risk_tier=risk,
        autonomy_tier=autonomy,
        effort_type=effort,
        required_proof_kinds=_proof_kinds(lowered, release_needed, telemetry_needed),
        tool_needs=_tool_needs(lowered),
        memory_needs=["project"],
        model_tier=_model_tier(risk),
        budget=2.0 if risk == RiskTier.HIGH else 1.0,
        token_budget=8000 if risk == RiskTier.HIGH else 10000,
        loop_depth=1 if autonomy == AutonomyTier.MANUAL else 3,
        release_needed=release_needed,
        telemetry_needed=telemetry_needed,
        clarification_questions=questions,
        mees_budget=_mees_budget(effort, risk),
    )


def _summary(text: str) -> str:
    return " ".join(text.split())[:200]


def _risk_tier(text: str) -> RiskTier:
    high_markers = ("security", "auth", "payment", "privacy", "production", "delete", "migration", "database")
    if any(marker in text for marker in high_markers):
        return RiskTier.HIGH
    if any(marker in text for marker in ("api", "release", "deploy", "integration")):
        return RiskTier.MEDIUM
    return RiskTier.LOW


def _effort_type(text: str) -> EffortType:
    if any(marker in text for marker in ("fix", "bug", "regression", "broken")):
        return EffortType.BUGFIX
    if any(marker in text for marker in ("refactor", "cleanup", "simplify")):
        return EffortType.REFACTOR
    if any(marker in text for marker in ("test", "coverage", "spec")):
        return EffortType.TEST
    if any(marker in text for marker in ("migrate", "migration")):
        return EffortType.MIGRATION
    if any(marker in text for marker in ("new project", "from scratch", "greenfield")):
        return EffortType.GREENFIELD
    return EffortType.FEATURE


def _autonomy_tier(risk: RiskTier, questions: list[str], text: str) -> AutonomyTier:
    if questions or risk == RiskTier.HIGH or any(marker in text for marker in ("delete", "drop", "credential")):
        return AutonomyTier.MANUAL
    if risk == RiskTier.MEDIUM:
        return AutonomyTier.BOUNDED
    return AutonomyTier.ASSISTED


def _proof_kinds(text: str, release_needed: bool, telemetry_needed: bool) -> list[str]:
    kinds = {"unit"}
    if any(marker in text for marker in ("invariant", "property", "random", "fuzz")):
        kinds.add("property")
    if any(marker in text for marker in ("api", "contract")):
        kinds.add("contract")
    if any(marker in text for marker in ("security", "auth", "privacy")):
        kinds.add("security")
    if release_needed:
        kinds.add("release")
    if telemetry_needed:
        kinds.add("telemetry")
    return sorted(kinds)


def _tool_needs(text: str) -> list[str]:
    tools = {"pytest"}
    if "docker" in text:
        tools.add("docker")
    if "git" in text:
        tools.add("git")
    return sorted(tools)


def _model_tier(risk: RiskTier) -> str:
    return "tier2" if risk == RiskTier.HIGH else "tier1"


def _mees_budget(effort: EffortType, risk: RiskTier) -> MeesBudget:
    return MeesBudget(
        minimum_score=80 if risk == RiskTier.HIGH else 70,
        max_changed_files=3 if effort == EffortType.BUGFIX else 5,
        allow_new_dependencies=False,
        requires_justification=risk == RiskTier.HIGH,
    )
