# OpenCode & AI CLI Integration (MCP)

OpenCode and other AI environments (such as Claude Code or Cursor) can interact with VAD through the Model Context Protocol (MCP). VAD ships with a built-in stdio-based MCP server.

## Installation & Configuration

To automatically install or configure VAD as an MCP server for your environment:

```bash
# Configure for all detected clients and print manual config snippets
vad mcp install

# Configure specifically for Claude Code CLI
vad mcp install claude

# Configure specifically for Cursor IDE
vad mcp install cursor
```

To run the MCP server manually (for client environments using stdio transports):

```bash
vad mcp run
```

## Exposed MCP Tools

The VAD MCP server exposes the following tools:

1. `validate_eip`: Validates a local EIP YAML or JSON file against the schemas and Pydantic invariants.
2. `run_proofs`: Executes the defined test suite or pytest targets to verify invariant obligations.
3. `retro_analyze`: Performs a retrospective analysis of an evidence bundle, generating learnings and persisting them to `MemoryScope.RETROSPECTIVE`.
4. `query_retrospective`: Queries historical retrospective learnings from the local memory store.
5. `submit_retrospective`: Manually records new learnings/constraint rules into the retrospective memory.

By exposing these tools, AI CLI agents and editor tools can act as fully verified agents within the VAD control system loops.
