from vad.policy.engine import PolicyEngine

class ModelGuard:
    def __init__(self, policy_path: str = "policies/vad.rego"):
        self.engine = PolicyEngine()
        self.policy_path = policy_path

    def check_model_access(self, model_tier: str) -> bool:
        input_data = {
            "action": "use_model",
            "model_tier": model_tier
        }
        
        # Query if model access is denied
        is_denied = self.engine.evaluate(self.policy_path, "data.vad.policy.deny_model_tier", input_data)
        if is_denied:
            return False
            
        # Also could check allow
        is_allowed = self.engine.evaluate(self.policy_path, "data.vad.policy.allow", input_data)
        return is_allowed is True
