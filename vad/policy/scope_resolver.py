"""Scope Resolver: Hierarchical governance policy merging across global, team, and repository scopes."""

from typing import Dict, List, Optional
from vad.policy.autonomy_policy import AutonomyPolicyResource, PolicyScope


class ScopeResolver:
    """Resolves hierarchical AutonomyPolicy inheritance: Global -> Team -> Repo.

    Safety invariant: Blocked tools are unioned (never overridden), and budget ceilings take the most restrictive bound.
    """

    def __init__(self, policies: Optional[List[AutonomyPolicyResource]] = None):
        self._global_policy: Optional[AutonomyPolicyResource] = None
        self._team_policies: Dict[str, AutonomyPolicyResource] = {}
        self._repo_policies: Dict[str, AutonomyPolicyResource] = {}

        if policies:
            for p in policies:
                self.add_policy(p)

    def add_policy(self, policy: AutonomyPolicyResource) -> None:
        target_type = policy.scope.target_type.lower()
        target_name = policy.scope.target_name.lower()

        if target_type == "global":
            self._global_policy = policy
        elif target_type == "team":
            self._team_policies[target_name] = policy
        elif target_type == "repo":
            self._repo_policies[target_name] = policy

    def resolve(self, repo_name: str, team_name: Optional[str] = None) -> AutonomyPolicyResource:
        hierarchy = []
        if self._global_policy:
            hierarchy.append(self._global_policy)

        if team_name and team_name.lower() in self._team_policies:
            hierarchy.append(self._team_policies[team_name.lower()])

        if repo_name.lower() in self._repo_policies:
            hierarchy.append(self._repo_policies[repo_name.lower()])

        if not hierarchy:
            return AutonomyPolicyResource(metadata={"name": f"default-{repo_name}"})

        # Merge hierarchy: start from base global, fold in team and repo
        merged = hierarchy[0].model_copy(deep=True)
        merged.scope = PolicyScope(target_type="repo", target_name=repo_name)

        for p in hierarchy[1:]:
            # 1. Blocked tools are ALWAYS UNIONED (cannot unblock upstream blocks)
            merged.blocked_tools = list(set(merged.blocked_tools + p.blocked_tools))

            # 2. Budget ceilings: take the most restrictive (minimum)
            merged.max_cost_ceiling_usd = min(merged.max_cost_ceiling_usd, p.max_cost_ceiling_usd)
            merged.max_token_ceiling = min(merged.max_token_ceiling, p.max_token_ceiling)

            # 3. Allowed tools: if more specific specifies explicit allowed list, override wildcard
            if p.allowed_tools and p.allowed_tools != ["*"]:
                merged.allowed_tools = p.allowed_tools

            # 4. HITL triggers are unioned
            merged.hitl_triggers.extend(p.hitl_triggers)

            # 5. Maturity level takes the repo-specific maturity level
            merged.maturity_level = p.maturity_level

        return merged
