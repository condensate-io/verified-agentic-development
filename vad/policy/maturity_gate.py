"""Maturity Gate: Enforces per-scope capability boundaries based on repository maturity level (L0-L4)."""

from typing import Dict, List, Optional
from pydantic import BaseModel, Field
from vad.policy.scope_resolver import ScopeResolver


class ActionMaturityRequirement(BaseModel):
    action_name: str
    required_level: int
    description: str


class MaturityGateResult(BaseModel):
    action_name: str
    repo_name: str
    current_maturity: int
    required_maturity: int
    permitted: bool
    reason: str


class MaturityGate:
    """Gates actions based on repository maturity:

    Level 0: Read-only inspection
    Level 1: Assisted editing with human-run tests
    Level 2: Bounded local container verification with staged commits
    Level 3: Parallel tranches and cross-harness DSSE attestation
    Level 4: Autonomous release promotion and continuous telemetry gating
    """

    DEFAULT_ACTION_LEVELS = {
        # Level 0 actions (read-only)
        "read_file": 0,
        "grep": 0,
        "explain_code": 0,
        "list_dir": 0,
        # Level 1 actions (local edits)
        "edit_file": 1,
        "write_file": 1,
        "run_linter": 1,
        # Level 2 actions (local container testing & staged commits)
        "run_docker_pytest": 2,
        "stage_commit": 2,
        "record_evidence": 2,
        # Level 3 actions (cross-harness review, parallel tranches, DSSE signing)
        "parallel_dispatch": 3,
        "cross_harness_review": 3,
        "dsse_sign": 3,
        # Level 4 actions (remote push and automated release gating)
        "confirm_push": 4,
        "promote_release": 4,
    }

    def __init__(
        self,
        scope_resolver: Optional[ScopeResolver] = None,
        custom_action_levels: Optional[Dict[str, int]] = None,
    ):
        self.scope_resolver = scope_resolver or ScopeResolver()
        self.action_levels = dict(self.DEFAULT_ACTION_LEVELS)
        if custom_action_levels:
            self.action_levels.update(custom_action_levels)

    def evaluate(
        self, repo_name: str, action_name: str, team_name: Optional[str] = None
    ) -> MaturityGateResult:
        resolved_policy = self.scope_resolver.resolve(repo_name=repo_name, team_name=team_name)
        current_level = resolved_policy.maturity_level

        required_level = self.action_levels.get(action_name.lower(), 2)
        permitted = current_level >= required_level

        if permitted:
            reason = (
                f"Action '{action_name}' permitted: repository maturity Level {current_level} "
                f"meets or exceeds required Level {required_level}"
            )
        else:
            reason = (
                f"Action '{action_name}' blocked: requires maturity Level {required_level}, "
                f"but repository '{repo_name}' is currently at Level {current_level}"
            )

        return MaturityGateResult(
            action_name=action_name,
            repo_name=repo_name,
            current_maturity=current_level,
            required_maturity=required_level,
            permitted=permitted,
            reason=reason,
        )
