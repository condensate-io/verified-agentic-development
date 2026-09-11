# How-To: Author and Customize Declarative Governance Policies

In VAD Enterprise, governance is **declarative and decoupled** from runner harnesses and application business logic. Policies are expressed as pure rules, validated by Open Policy Agent (OPA) and Python policy engines, and evaluated in real-time before, during, and after agent actions.

This guide provides a comprehensive manual for authoring, customizing, and enforcing governance policies across your repositories.

---

## 1. Governance Architecture Overview

```
       ┌────────────────────────────────────────────────────────┐
       │                 Incoming Agent Action                  │
       │     (Tool Call / File Edit / Shell Command / Release)  │
       └───────────────────────────┬────────────────────────────┘
                                   │
                                   ▼
       ┌────────────────────────────────────────────────────────┐
       │                Scope Resolver (Boundary)               │
       │    - Jailbreak / Traversal Check (../ escaping)        │
       │    - Directory & File Allowlists (spec.scope)          │
       └───────────────────────────┬────────────────────────────┘
                                   │ (Within Scope)
                                   ▼
       ┌────────────────────────────────────────────────────────┐
       │             Action Governance (Tool Perms)             │
       │    - Allowed / Denied / Conditional Tools              │
       │    - Role-Based Execution Limits (Builder vs Verifier) │
       └───────────────────────────┬────────────────────────────┘
                                   │ (Action Allowed)
                                   ▼
       ┌────────────────────────────────────────────────────────┐
       │                Maturity Gate (L1 - L5)                 │
       │    - Verifies Project Maturity Stage Obligations       │
       └───────────────────────────┬────────────────────────────┘
                                   │ (Execution Finishes)
                                   ▼
       ┌────────────────────────────────────────────────────────┐
       │                 Quality Gates (Patch)                  │
       │    - Max Line Delta | Max Complexity | Min Coverage    │
       │    - Unapproved Dependency Detection                   │
       └───────────────────────────┬────────────────────────────┘
                                   │
                                   ▼
       ┌────────────────────────────────────────────────────────┐
       │             LLM Semantic Invariant Hook                │
       │    - Evaluates Natural Language Architectural Invariants│
       └───────────────────────────┬────────────────────────────┘
                                   │
                                   ▼
       ┌────────────────────────────────────────────────────────┐
       │              Policy Decision (Allow / Deny)            │
       │    - Allow: Commit / Promote                           │
       │    - Deny: Block / Rollback / Escalate to Operator     │
       └────────────────────────────────────────────────────────┘
```

---

## 2. Customizing Quality Gates (`vad/policy/quality_gates.py`)

Quality gates enforce deterministic statistical and static analysis standards on every patch before it is eligible for review.

### Configuration Schema
```python
from vad.policy.quality_gates import QualityGateConfig, QualityGateEngine

# Define enterprise quality thresholds
config = QualityGateConfig(
    max_line_delta=300,                  # Maximum total added/modified lines
    max_complexity_delta=3,              # Maximum cyclomatic complexity increase
    min_test_coverage_percent=85.0,      # Minimum test coverage required
    forbid_new_unapproved_dependencies=True,
    approved_dependency_licenses=["MIT", "Apache-2.0", "BSD-3-Clause"],
    forbidden_import_patterns=["telnetlib", "pickle", "eval("]
)

engine = QualityGateEngine(config)
```

### Evaluating a Patch
```python
patch_metadata = {
    "line_delta": 142,
    "complexity_delta": 1,
    "test_coverage": 92.4,
    "new_dependencies": ["pydantic"],
    "dependency_licenses": {"pydantic": "MIT"},
    "source_diff": "import pydantic\n..."
}

result = engine.evaluate_patch(patch_metadata)
if not result.passed:
    print("Quality Gate Rejected:")
    for violation in result.violations:
        print(f" - {violation}")
else:
    print("Quality Gate Passed.")
```

---

## 3. Action Governance: Allowed, Denied, and Conditional Actions

Action governance inspects tool invocations in real-time via the `PreToolUse` hook. It classifies operations into:
- **`allow`**: Safe read-only or scoped operations.
- **`deny`**: Prohibited destructive or unconstrained commands.
- **`require_human`**: Elevated operations requiring operator override in the TUI.

### Declarative Policy Rules (`policies/action_governance.yaml`)
```yaml
version: "1.0"
roles:
  builder:
    allowed_tools: ["view_file", "replace_file_content", "write_to_file", "run_command"]
    forbidden_tools: ["approve_release", "deploy_apply"]
  verifier:
    allowed_tools: ["view_file", "grep_search", "run_command"]
    forbidden_tools: ["write_to_file", "replace_file_content"]

rules:
  # Block destructive filesystem commands
  - action: "bash.execute"
    condition: "command =~ r'(?i)(rm\s+-rf|dd\s+if=|mkfs|chmod\s+777)'"
    decision: "deny"
    reason: "Destructive filesystem operations are strictly forbidden."

  # Block unapproved network egress
  - action: "bash.execute"
    condition: "command =~ r'(?i)(curl|wget|nc|ssh)\s+' and not command contains '127.0.0.1'"
    decision: "deny"
    reason: "External network requests outside local loopback are prohibited."

  # Guard CI/CD pipelines
  - action: "file.write"
    condition: "path.startswith('.github/workflows/') or path.startswith('.gitlab-ci.yml')"
    decision: "require_human"
    reason: "CI/CD configuration modifications require human operator approval."
```

### Python Evaluation Example
```python
from vad.policy.action_governance import ActionGovernanceEngine

governor = ActionGovernanceEngine.load("policies/action_governance.yaml")

decision = governor.evaluate_action(
    actor="claude_code_builder",
    role="builder",
    action="bash.execute",
    arguments={"command": "rm -rf /tmp/test"}
)

print(f"Allowed: {decision.allow}")          # False
print(f"Denials: {decision.denials}")        # ['Destructive filesystem operations are strictly forbidden.']
```

---

## 4. Maturity Progression Gates L1–L5 (`vad/policy/maturity_gate.py`)

VAD defines a formal maturity model for autonomous agent autonomy. Agents cannot perform actions reserved for higher maturity levels without meeting prerequisite verifications.

| Maturity Level | Level Name | Key Capabilities | Mandatory Gates |
|----------------|------------|------------------|-----------------|
| **Level 1** | Ad-Hoc Assistant | Inline chat, code explanations | Local syntax check |
| **Level 2** | Governed Builder | Scoped file modifications, test runs | Pre/PostTool hooks, Scope boundary |
| **Level 3** | Verified Autonomous | Autonomous task loop execution | Disposable container testing, DSSE attestation |
| **Level 4** | Parallel Swarm | Multi-agent DAG tranches, crash recovery | Heartbeat leasing, DoR gating |
| **Level 5** | Autonomous Deployment | Automated production promotion | 2-Person release rule (`operator != builder`) |

### Enforcing Level Gates
```python
from vad.policy.maturity_gate import MaturityGate

gate = MaturityGate(current_level=3)

# Attempting L5 action (production deploy) from an L3 run
decision = gate.evaluate_action_level(
    required_level=5,
    action="deploy_apply",
    context={"environment": "production"}
)

if not decision.allow:
    print(f"Maturity Violation: {decision.denials}")
    # Output: ["Action 'deploy_apply' requires Maturity Level 5, but project is at Level 3"]
```

---

## 5. Scope Resolver & Workspace Boundary Constraints

The `ScopeResolver` prevents directory traversal attacks and restricts modifications strictly to the active task's declared scope.

```python
from vad.policy.scope_resolver import ScopeResolver

resolver = ScopeResolver(
    workspace_root=".",
    allowed_patterns=["vad/decision/*.py", "tests/test_decision_*.py"],
    denied_patterns=[".git/**", ".env*", "secrets/**", "compose.yaml"]
)

# Safe file within scope
assert resolver.is_path_allowed("vad/decision/router.py") is True

# Forbidden secret file
assert resolver.is_path_allowed(".env.production") is False

# Path traversal attempt
assert resolver.is_path_allowed("../../../etc/passwd") is False
```

---

## 6. Authoring LLM Semantic Invariant Hooks (`vad/policy/llm_hooks.py`)

Static analysis cannot always catch subtle architectural violations (e.g. "Do not bypass the connection pool"). LLM Hooks evaluate natural language invariants against patch diffs:

```python
from vad.policy.llm_hooks import LLMInvariantChecker

checker = LLMInvariantChecker()

invariant = (
    "All SQLite access in ServerStore must configure WAL journal mode "
    "and set busy_timeout to prevent locking under WSL2 concurrent execution."
)

code_patch = '''
+    def _connect(self) -> sqlite3.Connection:
+        conn = sqlite3.connect(self.db_path, timeout=30.0)
+        conn.execute("PRAGMA journal_mode = WAL")
+        conn.execute("PRAGMA busy_timeout = 30000")
+        return conn
'''

result = checker.check_invariant(code_diff=code_patch, invariant=invariant)
assert result.satisfied is True
```

---

## 7. Autonomy Policies & Financial Circuit Breakers

To guard against runaway spending or infinite retry loops:

```python
from vad.policy.autonomy_policy import AutonomyPolicy

policy = AutonomyPolicy(
    max_iterations_per_task=25,
    max_tokens_per_run=250000,
    max_cost_usd=15.00,
    max_execution_time_seconds=1800
)

# Evaluated on each agent loop iteration
status = policy.check_limits(current_iterations=12, current_cost=4.20)
if status.circuit_breaker_triggered:
    raise RuntimeError(f"Circuit Breaker Triggered: {status.reason}")
```

---

## 8. Authoring a Custom Policy Rule: Step-by-Step

### Step 1: Define the Policy in `policies/vad.rego`
```rego
package vad.custom

default allow = false

# Allow file edits only if author is not an untrusted agent
allow {
    input.action == "file_write"
    input.actor_role == "builder"
    not contains(input.path, "sensitive_config")
}
```

### Step 2: Test Policy with OPA CLI
```bash
opa eval -d policies/ -i '{"action": "file_write", "actor_role": "builder", "path": "src/app.py"}' "data.vad.custom.allow"
```

### Step 3: Integrate with Python Engine
The policy is loaded directly into `vad/policy/engine.py` and executed seamlessly by `PreToolUse` and `DoRGate`.
