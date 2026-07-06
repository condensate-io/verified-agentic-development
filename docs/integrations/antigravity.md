# Antigravity Integration Discovery

VAD treats Antigravity as a known local coding client type, but current public
Antigravity documentation does not expose a stable local MCP, plugin, or config
file surface that VAD can honestly target with a first-class package generator.

No first-class VAD Antigravity package is generated at this time. Use the
generic stdio MCP package until Antigravity documents a local integration
contract.

## Discovery Result

Checked on 2026-07-03:

- the public Antigravity product surface;
- public searches for Antigravity MCP, plugin, and config-file documentation;
- local VAD integration and client-registry docs.

The available public material describes Antigravity as an agent-first coding
environment with editor, terminal, browser, manager, and artifact surfaces. It
does not document a stable user or workspace config file for registering a
local MCP server, nor a plugin/package format that VAD can generate.

## Generic MCP Fallback

Use the generic VAD MCP package id `vad-generic-mcp`. If Antigravity adds a
manual MCP server field or generic stdio server map, use:

```json
{
  "mcpServers": {
    "vad": {
      "command": "vad",
      "args": ["mcp", "run"],
      "env": {
        "VAD_LIVE_SERVICES": "disabled"
      }
    }
  }
}
```

If Antigravity only accepts a direct command, run:

```bash
vad mcp run
```

## Safety Notes

Do not claim first-class Antigravity support until a current Antigravity doc
defines one of:

- a stable local MCP config path and schema;
- a plugin/package manifest VAD can generate;
- a documented command-line or SDK integration surface for local tools.

Until then, Antigravity remains a generic MCP/manual client for VAD. High-risk
tools such as repository patching and signing verification remain hidden unless
the caller supplies the matching role and explicit high-risk approval for that
call.

Sources checked:

- <https://antigravity.google/>
- public search for Antigravity MCP/config/plugin documentation
- [Generic MCP Package](generic_mcp.md)
