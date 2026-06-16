import json
import subprocess
from typing import Any, Dict

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
            raise RuntimeError(f"OPA evaluation failed: {result.stderr}")
            
        try:
            output = json.loads(result.stdout)
            if "result" in output and len(output["result"]) > 0:
                # Typically, OPA returns [{"expressions": [{"value": ...}]}]
                return output["result"][0]["expressions"][0]["value"]
            return None
        except json.JSONDecodeError:
            raise RuntimeError(f"Failed to parse OPA output: {result.stdout}")
