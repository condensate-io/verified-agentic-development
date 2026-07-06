# Provider And MCP Integration Scope

VAD supports two model-facing paths:

- direct upstream provider adapters, behind optional dependencies and explicit policy gates;
- MCP client integration, where coding tools call VAD tools but do not bypass VAD controls.

## Direct Upstream Providers

Provider adapters must:

- expose inventory, model capabilities, context limits, and token/cost metadata;
- redact sensitive prompt fields in evidence;
- record provider request ids, token usage, cost, retry/timeout decisions, and drift warnings;
- keep live calls opt-in, credential-gated, and skipped in default tests.

Current status:

- OpenAI: optional SDK adapter with injected-client tests and opt-in live smoke.
- Additional upstream providers: planned, not implemented.

## Upstream Provider Matrix

| Provider | Runtime status | Package boundary | Default tests | Live smoke gate | Notes |
|---|---|---|---|---|---|
| Fake | implemented | built-in | deterministic | none | Offline contract provider for routing, budget, and evidence tests. |
| OpenAI | implemented optional adapter | `openai` extra | injected fake client | `VAD_LIVE_OPENAI_PROVIDER_TEST=1` | Direct SDK calls are credential-gated and skipped by default. |
| Anthropic | planned | not added | none | none | Future adapter must follow the same inventory, redaction, evidence, and opt-in live-test contract. |
| Google Gemini | planned | not added | none | none | Future adapter must not add default network calls or credentials. |
| Azure OpenAI | planned | not added | none | none | Future adapter must separate deployment name, model capability metadata, and secret references. |
| AWS Bedrock | planned | not added | none | none | Future adapter must use explicit region and credential references only. |
| Local OpenAI-compatible endpoint | planned | not added | none | none | Future adapter must require an explicit base URL allowlist. |

Default CI must never call live providers. New SDK packages require explicit approval and must include deterministic injected-client tests before any opt-in smoke test is added.

## MCP Coding Clients

MCP is the integration surface for editor and CLI agents:

- VSCode MCP-capable extensions: manual stdio MCP configuration.
- Windsurf: supported by the generated local package and manual stdio MCP configuration.
- Claude Code: supported by `vad mcp install claude` and manual stdio configuration.
- OpenCode: supported by the generated local package and manual stdio MCP configuration.
- Cursor: supported by the generated local package, `vad mcp install cursor`, and manual stdio configuration.
- Antigravity: no current first-class VAD package; use the generic stdio MCP fallback if Antigravity exposes a manual local server entry.
- Generic MCP clients: run `vad mcp run` as a stdio server.

Coding clients are actuator surfaces. They must use VAD policy, repository sandboxing, proof gates, signing verification, provider budgets, swarm separation of duties, and deployment approvals.

## Client Capability Matrix

| Client | Config path | Tool calls | File edits | Approvals | Notes |
|---|---|---:|---:|---:|---|
| Claude Code | `vad mcp install claude` | yes | client-controlled | VAD-gated | Installer writes local client config when possible. |
| Cursor | generated `.cursor/mcp.json` package or `vad mcp install cursor` | yes | client-controlled | VAD-gated | Package dry-run lists project/global config and rules; legacy installer writes local client config when possible. |
| OpenCode | generated `opencode.jsonc` package | yes | client-controlled | VAD-gated | Package dry-run lists project config, Markdown agents, and per-agent VAD tool permission gates. |
| VSCode MCP extensions | manual stdio MCP config | depends on extension | client-controlled | VAD-gated | Keep secrets out of workspace settings. |
| Windsurf | generated `.codeium/windsurf/mcp_config.json` package | yes when MCP is enabled | client-controlled | VAD-gated | Package dry-run lists user MCP config, workspace rules, and verification workflow files. |
| Generic stdio MCP client | manual stdio MCP config | yes | client-controlled | VAD-gated | Command is `vad mcp run`. |
| Antigravity | generic stdio MCP fallback only | unknown | client-controlled | VAD-gated | Current public docs do not expose a stable MCP/plugin/config surface for first-class packaging. |

Manual stdio config shape:

```json
{
  "mcpServers": {
    "vad": {
      "command": "vad",
      "args": ["mcp", "run"]
    }
  }
}
```

Do not place API keys, signing secrets, deployment credentials, or production approval tokens in MCP config files. Use environment-specific secret managers or runtime environment variables outside the repository.

## Command Coverage

- Tested: fake provider routing, OpenAI injected-client adapter behavior, `vad mcp run`, `vad mcp install claude`, and `vad mcp install cursor`.
- Opt-in only: live OpenAI smoke tests require `VAD_LIVE_OPENAI_PROVIDER_TEST=1` and credentials outside the repository.
- Illustrative: manual MCP JSON snippets show shape only and must be adapted to each client.
