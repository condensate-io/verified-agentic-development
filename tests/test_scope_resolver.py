import pytest
from vad.policy.autonomy_policy import AutonomyPolicyResource, PolicyScope
from vad.policy.scope_resolver import ScopeResolver


def test_scope_resolver_unions_blocked_tools():
    # Global blocks rm_rf
    global_p = AutonomyPolicyResource(
        metadata={"name": "global"},
        scope=PolicyScope(target_type="global", target_name="*"),
        blocked_tools=["rm_rf"],
        max_cost_ceiling_usd=10.0,
    )
    # Team blocks git_push
    team_p = AutonomyPolicyResource(
        metadata={"name": "team-voice"},
        scope=PolicyScope(target_type="team", target_name="voice"),
        blocked_tools=["git_push"],
        max_cost_ceiling_usd=5.0,
    )
    # Repo blocks curl
    repo_p = AutonomyPolicyResource(
        metadata={"name": "repo-telephony"},
        scope=PolicyScope(target_type="repo", target_name="telephony-service"),
        blocked_tools=["curl"],
        max_cost_ceiling_usd=8.0,  # Team ceiling 5.0 is lower and should win
    )

    resolver = ScopeResolver([global_p, team_p, repo_p])
    resolved = resolver.resolve(repo_name="telephony-service", team_name="voice")

    # Invariant: All blocked tools from all scopes are preserved
    assert "rm_rf" in resolved.blocked_tools
    assert "git_push" in resolved.blocked_tools
    assert "curl" in resolved.blocked_tools

    # Invariant: Most restrictive budget ceiling wins
    assert resolved.max_cost_ceiling_usd == 5.0
