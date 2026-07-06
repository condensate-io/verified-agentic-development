# Local Install Guide

This guide installs VAD as a local-only toolchain. It does not require a cloud VAD service, hosted dashboard, managed tenant, remote MCP gateway, live model credential, or marketplace package.

## Local Prerequisites

Use a local Python environment or the project Docker image. From the repository
root:

```bash
pip install -e ".[dev]"
vad --help
```

For a disposable verification path, use the default Docker gate:

```bash
docker build -t vad-test:local .
docker run --rm vad-test:local
```

To run the local operating-system demo:

```bash
vad local-os demo
```

The demo binds to `127.0.0.1` by default, stores state under `.vad/local-os`,
uses local SQLite/filesystem state, and seeds deterministic simulator data. It
does not call cloud APIs or start paid model clients.

## Manual Fallbacks

Every target has a manual local fallback. Use these before any future installer
apply path, marketplace package, or registry distribution.

| Target | Local package | Manual fallback | Notes |
| --- | --- | --- | --- |
| Core CLI | `verified-agentic-development` | `pip install -e ".[dev]"` then `vad --help` | Local Python package only; PyPI is not approved for the current local distribution. |
| Generic MCP clients | `vad-generic-mcp` | `vad mcp run` | Use stdio MCP config from `docs/integrations/generic_mcp.md`. |
| Claude Code | `vad-claude-code-local` | `vad mcp run` with workspace `.mcp.json` | Review `docs/integrations/claude_code.md` before adding config. |
| Codex | `vad-codex-local` | `vad mcp run` with local plugin/skill files | Review `docs/integrations/codex.md`; no automatic plugin install is performed. |
| VS Code | `vad-vscode-local` | `vad mcp run` with `.vscode/mcp.json` | Review workspace trust and `docs/integrations/vscode.md`. |
| Cursor | `vad-cursor-local` | `vad mcp run` with `.cursor/mcp.json` | Review `.cursor/rules` and `docs/integrations/cursor.md`. |
| Windsurf | `vad-windsurf-local` | `vad mcp run` with Windsurf MCP config | Review rules/workflows in `docs/integrations/windsurf.md`. |
| OpenCode | `vad-opencode-local` | `vad mcp run` with `opencode.jsonc` | Review per-agent permissions in `docs/integrations/opencode.md`. |
| Antigravity | generic MCP fallback | `vad mcp run` | No first-class package is generated; use `docs/integrations/antigravity.md`. |

## Artifact Review

For reviewable local artifacts, build deterministic metadata and manifests:

```python
from pathlib import Path
from vad.control_plane.artifacts import build_reproducible_artifacts

build_reproducible_artifacts(Path(".vad/artifacts"))
```

Review `.vad/artifacts/digest-report.json`, generated plugin manifests, dry-run
installer output, and rollback metadata before copying any config into a client.
Publication decisions are recorded in `docs/publication-decisions.md`; they do not approve publishing.

## MCP Security Warnings

Local MCP servers run commands on the operator machine with the permissions of
the client process. Treat MCP config like executable code:

- keep `VAD_LIVE_SERVICES=disabled` unless a human explicitly opts into live
  services outside the current local distribution;
- do not put API keys, tokens, passwords, signing secrets, private keys, or provider credentials in MCP config files;
- review every generated config path before copying it into user or workspace
  settings;
- keep high-risk tools approval-gated and do not approve repository patching,
  deployment, signing, or release actions by default;
- use temp-home or dry-run installer checks before touching real user config;
- remove copied config manually or follow the dry-run rollback metadata if a
  package is no longer trusted.

No current VAD command publishes packages, installs marketplace extensions, trusts generated artifacts automatically, or writes real user config without a human operator applying the reviewed files.
