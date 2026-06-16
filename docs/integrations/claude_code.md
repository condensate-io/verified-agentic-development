# Claude Code CLI Integration

Anthropic's Claude Code CLI can be integrated into the VAD workflow as an autonomous builder and verifier.

## MCP Server Installation

To natively integrate VAD tools as functions/tools directly inside Claude Code (allowing Claude to call them autonomously), run the following installer:

```bash
vad mcp install claude
```

This will automatically add VAD as a tool provider in Claude Code's configuration file (`~/.claudecode/config.json`).

Once installed, Claude Code will have access to the VAD toolset (`validate_eip`, `run_proofs`, `retro_analyze`, `query_retrospective`, and `submit_retrospective`) during its session.

## Guidance for Claude Code

When running Claude Code within a VAD project:

1. **Context Loading**: Always start your prompt by asking Claude to review the relevant EIPs and `schemas/eip.schema.json`.
2. **Verification Loop**: Instruct Claude to run `vad eip validate <target-eip>` and `pytest` after generating code.
3. **Retro Integration**: If Claude encounters repeated failures or architectural constraints, instruct it to run `vad eip retro` or document the learnings in the retrospective memory scope.

Example Prompt:
> "Act as a VAD Builder Agent. Review `my-feature/eip.yaml` and implement the required logic. Run `vad eip validate` and `pytest` to ensure all proof obligations pass."

