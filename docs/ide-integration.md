# AI IDE Integration

Verified Agentic Development (VAD) is designed to constrain and guide AI-assisted coding tools.

## How AI IDEs Integrate with VAD

AI IDEs and CLI coding agents (such as VSCode MCP-capable extensions, Windsurf, Claude Code, OpenCode, Cursor, or plugins like GitHub Copilot) act as the "Actuator" or Builder Agent in the VAD control system.

Instead of allowing free-form codebase modifications, AI IDEs integrated with VAD must:
1. **Read the EIP**: Understand the intent, constraints, and proof obligations before writing code.
2. **Follow Schemas**: Validate any new or modified EIPs against `schemas/eip.schema.json`.
3. **Check Memory**: Look up past learnings from the retrospective loop (`MemoryScope.RETROSPECTIVE`).
4. **Implement Proofs First**: Ensure tests (proof obligations) are established before core logic.

See specific integration guides for your tool:
- [Cursor Integration](integrations/cursor.md)
- [Claude Code Integration](integrations/claude_code.md)
- [Generic MCP Package](integrations/generic_mcp.md)
- [VS Code Integration](integrations/vscode.md)
- [Windsurf Integration](integrations/windsurf.md)
- [OpenCode Integration](integrations/opencode.md)
- [Antigravity Discovery](integrations/antigravity.md)
- [Provider And MCP Integration Scope](providers.md)

MCP clients must not bypass VAD controls. Repository writes, model calls, signatures, swarm actions, and deployments remain governed by VAD policy and evidence gates.

## Role Mapping

Coding clients can act as VAD roles only within their granted capabilities:

| VAD role | Expected client behavior |
|---|---|
| Planner | Read ask/EIP context, propose bounded tasks, avoid writes. |
| Builder | Modify files only through approved repo automation scope. |
| Verifier | Run proof obligations and report evidence. |
| Auditor | Inspect evidence, policy decisions, signatures, and recovery state. |
| Release guardian | Check deployment gates and approvals; never self-approve builder work. |
