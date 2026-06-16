from vad.policy.engine import PolicyEngine

class MemoryGuard:
    def __init__(self, policy_path: str = "policies/vad.rego"):
        self.engine = PolicyEngine()
        self.policy_path = policy_path

    def check_memory_access(self, scope: str) -> bool:
        input_data = {
            "action": "memory_access",
            "scope": scope
        }
        
        is_denied = self.engine.evaluate(self.policy_path, "data.vad.policy.deny_memory_scope", input_data)
        if is_denied:
            return False
            
        is_allowed = self.engine.evaluate(self.policy_path, "data.vad.policy.allow", input_data)
        return is_allowed is True
