import json
import subprocess
from typing import Any, Dict

from vad.policy.decisions import PolicyDecision, PolicyEvaluationError

class PolicyEngine:
    def __init__(self, policy_dir: str = "policies"):
        self.policy_dir = policy_dir

    def evaluate(self, policy_path: str, query: str, input_data: Dict[str, Any]) -> Any:
        """
        Evaluates a Rego policy using the `opa` CLI.
        """
        input_json = json.dumps(input_data)
        
        # opa eval -i /dev/stdin -d <policy_path> '<query>'
        cmd = [
            "opa", "eval",
            "-d", policy_path,
            "-I",  # read input from stdin
            query
        ]
        
        result = subprocess.run(
            cmd,
            input=input_json,
            text=True,
            capture_output=True,
            check=False
        )
        
        if result.returncode != 0:
            raise PolicyEvaluationError(f"OPA evaluation failed: {result.stderr}")
            
        try:
            output = json.loads(result.stdout)
            if "result" in output and len(output["result"]) > 0:
                # Typically, OPA returns [{"expressions": [{"value": ...}]}]
                return output["result"][0]["expressions"][0]["value"]
            return None
        except json.JSONDecodeError:
            raise PolicyEvaluationError(f"Failed to parse OPA output: {result.stdout}")

    def evaluate_decision(self, policy_path: str, input_data: Dict[str, Any]) -> PolicyDecision:
        deny_queries = {
            "data.vad.policy.deny_builder_self_approval": "builder may not self-approve",
            "data.vad.policy.deny_unauthorized_tool": "tool access denied",
            "data.vad.policy.deny_model_tier": "model tier denied",
            "data.vad.policy.deny_memory_scope": "memory scope denied",
            "data.vad.policy.deny_unauthorized_delegation": "delegation capability denied",
        }
        denials = [
            reason
            for query, reason in deny_queries.items()
            if self.evaluate(policy_path, query, input_data) is True
        ]
        if denials:
            return PolicyDecision(allow=False, denials=denials)

        allowed = self.evaluate(policy_path, "data.vad.policy.allow", input_data) is True
        return PolicyDecision(
            allow=allowed,
            reasons=["policy allow rule matched"] if allowed else [],
            denials=[] if allowed else ["no allow rule matched"],
        )

    def evaluate_dependency_change(self, has_dependency_changes: bool, approved: bool) -> PolicyDecision:
        if has_dependency_changes and not approved:
            return PolicyDecision(
                allow=False,
                denials=["dependency changes require explicit approval"],
                requires_human=True,
            )
        return PolicyDecision(allow=True, reasons=["dependency change policy satisfied"])

    def evaluate_deployment(
        self,
        *,
        action: str,
        environment: str,
        approval_ref: str | None = None,
        telemetry_count: int = 0,
        rollback_enabled: bool = False,
        rollback_approval_ref: str | None = None,
    ) -> PolicyDecision:
        if action == "deploy_dry_run":
            return PolicyDecision(allow=True, reasons=["deployment dry-run is allowed"])
        if action == "deploy_rollback":
            if not rollback_approval_ref:
                return PolicyDecision(
                    allow=False,
                    denials=["deployment rollback requires rollback approval"],
                    requires_human=True,
                )
            return PolicyDecision(allow=True, reasons=["deployment rollback approval satisfied"])
        if action != "deploy_apply":
            return PolicyDecision(allow=False, denials=["unsupported deployment action"])

        denials = []
        if environment == "production":
            if not approval_ref:
                denials.append("production deployment requires approval")
            if telemetry_count <= 0:
                denials.append("production deployment requires telemetry")
            if not rollback_enabled:
                denials.append("production deployment requires rollback")
        if denials:
            return PolicyDecision(allow=False, denials=denials, requires_human=True)
        return PolicyDecision(allow=True, reasons=["deployment policy satisfied"])
