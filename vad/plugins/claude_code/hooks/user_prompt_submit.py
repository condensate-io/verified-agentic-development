"""UserPromptSubmit Hook: Analyzes user prompts with the hybrid router and injects policy context."""

import json
import sys
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field
from vad.decision.hybrid_router import HybridRouter
from vad.policy.autonomy_policy import AutonomyPolicyEvaluator, AutonomyPolicyResource


class UserPromptSubmitResult(BaseModel):
    original_prompt: str
    enriched_prompt: str
    routing_suggestion: Dict[str, Any]
    context_added: List[str] = Field(default_factory=list)


class UserPromptSubmitHook:
    """Claude Code UserPromptSubmit hook routing queries and injecting enterprise policy invariants."""

    def __init__(
        self,
        hybrid_router: Optional[HybridRouter] = None,
        policy_evaluator: Optional[AutonomyPolicyEvaluator] = None,
    ):
        self.hybrid_router = hybrid_router or HybridRouter()
        self.policy_evaluator = policy_evaluator or AutonomyPolicyEvaluator(AutonomyPolicyResource())

    def process(self, payload: Dict[str, Any]) -> UserPromptSubmitResult:
        prompt = payload.get("prompt", "")
        repo = payload.get("repo", "")

        # 1. Run hybrid router on prompt
        route_dec = self.hybrid_router.route(prompt)
        routing_meta = route_dec.model_dump()

        # 2. Extract policy constraints
        context_items = []
        policy = self.policy_evaluator.policy
        if policy.blocked_tools:
            context_items.append(f"Blocked tools for this workspace: {', '.join(policy.blocked_tools)}")
        context_items.append(f"Autonomy maturity level: {policy.maturity_level}")
        if route_dec.match_id:
            context_items.append(
                f"Recommended domain specialist: `{route_dec.match_id}` (runner: {route_dec.target_agent or 'general'})"
            )

        # 3. Construct enriched prompt
        guidance_header = (
            "--- [VAD SYSTEM CONTEXT & GOVERNANCE INVARIANTS] ---\n"
            + "\n".join(f"- {item}" for item in context_items)
            + "\n----------------------------------------------------\n\n"
        )
        enriched = guidance_header + prompt

        return UserPromptSubmitResult(
            original_prompt=prompt,
            enriched_prompt=enriched,
            routing_suggestion=routing_meta,
            context_added=context_items,
        )


def main(argv: Optional[list] = None) -> int:
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
    except Exception:
        payload = {}

    hook = UserPromptSubmitHook()
    result = hook.process(payload)
    print(json.dumps(result.model_dump()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
