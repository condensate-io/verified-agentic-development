# AI IDE Integration

Verified Agentic Development (VAD) is designed to constrain and guide AI-assisted coding tools.

## How AI IDEs Integrate with VAD

AI IDEs (such as Cursor, Windsurf, or plugins like GitHub Copilot) act as the "Actuator" or Builder Agent in the VAD control system.

Instead of allowing free-form codebase modifications, AI IDEs integrated with VAD must:
1. **Read the EIP**: Understand the intent, constraints, and proof obligations before writing code.
2. **Follow Schemas**: Validate any new or modified EIPs against `schemas/eip.schema.json`.
3. **Check Memory**: Look up past learnings from the retrospective loop (`MemoryScope.RETROSPECTIVE`).
4. **Implement Proofs First**: Ensure tests (proof obligations) are established before core logic.

See specific integration guides for your tool:
- [Cursor Integration](../.cursorrules)
- [Claude Code Integration](integrations/claude_code.md)
- [OpenCode Integration](integrations/opencode.md)
