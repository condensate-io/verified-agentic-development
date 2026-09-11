# How-To: Install and Customize the Claude Code VAD Plugin

The **Claude Code VAD Plugin** seamlessly installs governance hooks, custom slash commands, specialized review subagents, and Model Context Protocol (MCP) integrations directly into the developer's local Claude Code installation (`~/.claude`).

---

## 1. The 7 Governed Lifecycle Hooks

The plugin registers 7 deterministic lifecycle hooks to intercept agent activity:

| Hook Name | Lifecycle Trigger | Governance Function |
|-----------|-------------------|---------------------|
| `PreToolUse` | Before tool call execution | Validates tool authorization, checks file boundaries, prevents escaping |
| `PostToolUse` | After tool call completes | Validates output, redacts secrets, records audit telemetry |
| `Stop` | When agent attempts to finish | Verifies that Definition of Done (DoD) obligations are satisfied |
| `SubagentStop` | When a child subagent stops | Validates subagent findings and merges evidence |
| `Notification` | System/alert notification | Routes alerts and operator escalations |
| `UserPromptSubmit` | User submits a prompt | Enforces DoR guidelines, injects project invariants |
| `SessionStart` | Claude Code CLI launch | Injects active enterprise policies, verifies SkillGuard integrity |

---

## 2. Custom Slash Commands

The plugin provides slash commands for engineers to govern the session interactively:

- `/vad-plan`: Generates or validates an EIP draft for the current ask.
- `/vad-verify`: Executes verification tests in a throwaway Docker container.
- `/vad-status`: Displays current token consumption, active leases, and DoR status.
- `/vad-decision`: Inspects why a particular tool or harness route was selected.

---

## 3. Specialized Review Subagents

Located under `vad/plugins/claude_code/review_agents.py`, these subagents can be invoked directly:

- `architecture_auditor`: Verifies layered architecture and prevents circular imports.
- `security_reviewer`: Audits AST for secret leakage, SQL injection, and path traversal.
- `performance_analyst`: Inspects time complexity, memory allocation, and database locks.

---

## 4. MCP Integration and Tool Governance

VAD exposes its control plane as an MCP server:
```json
{
  "mcpServers": {
    "vad-control-plane": {
      "command": "python",
      "args": ["-m", "vad.adapters.mcp"]
    }
  }
}
```

---

## 5. Automated Plugin Installation

To install or update the VAD plugin in your Claude Code environment:
```bash
python -m vad.plugins.claude_code.settings_injector --install
```
