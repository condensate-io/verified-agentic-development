import os
from vad.policy.engine import PolicyEngine

class ToolGuard:
    def __init__(self, policy_path: str = "policies/vad.rego"):
        self.engine = PolicyEngine()
        self.policy_path = policy_path

    def check_tool_access(self, tool_name: str) -> bool:
        input_data = {
            "action": "use_tool",
            "tool": tool_name
        }
        
        # Query if tool access is denied
        is_denied = self.engine.evaluate(self.policy_path, "data.vad.policy.deny_unauthorized_tool", input_data)
        if is_denied:
            return False
            
        return True
