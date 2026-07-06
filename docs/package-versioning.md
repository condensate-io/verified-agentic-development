# Package And Versioning Policy

This policy defines the local-first version streams for the VAD core package,
client integration packages, schema artifacts, and compatibility promises. It is
not a publication approval. Publishing to PyPI, npm, VS Code Marketplace,
OpenVSX, a Claude marketplace, or any other registry remains a separate
human-approved release decision.

## Version Streams

| Artifact | Current version | Source of truth | Compatibility rule |
| --- | --- | --- | --- |
| Core Python package | `0.1.0` | `pyproject.toml` `[project].version` | Minor versions may add CLI/API fields and local commands; patch versions must keep existing CLI flags, SQLite migrations, and local API response fields backward compatible. |
| Generic MCP package | `1.0.0` | `vad.control_plane.generic_mcp_package` manifest | Major versions may change generated config shape; minor/patch versions must keep `vad mcp run` and documented manual fallback compatible. |
| Claude Code package | `1.0.0` | `vad.control_plane.claude_code_package` manifest | Minor/patch versions must preserve local `.mcp.json` semantics and reviewable prompt paths. |
| Codex package | `1.0.0` | `vad.control_plane.codex_package` manifest | Minor/patch versions must preserve local plugin/skill paths and stdio MCP command semantics. |
| VS Code package | `1.0.0` | `vad.control_plane.vscode_package` manifest | Minor/patch versions must preserve `.vscode/mcp.json` and reviewable task semantics. |
| Cursor package | `1.0.0` | `vad.control_plane.cursor_package` manifest | Minor/patch versions must preserve project MCP config and `.cursor/rules` review semantics. |
| Windsurf package | `1.0.0` | `vad.control_plane.windsurf_package` manifest | Minor/patch versions must preserve documented user MCP config, rules, and workflow semantics. |
| OpenCode package | `1.0.0` | `vad.control_plane.opencode_package` manifest | Minor/patch versions must preserve local `opencode.jsonc` MCP command and per-agent permission gates. |

Antigravity does not have a first-class generated package in the current local distribution. It uses
the Generic MCP package until a documented local package/config surface exists.

Current generated package ids are `vad-generic-mcp`, `vad-claude-code-local`,
`vad-codex-local`, `vad-vscode-local`, `vad-cursor-local`,
`vad-windsurf-local`, and `vad-opencode-local`.

## Schema Versions

`schemas/vad-plugin-manifest.schema.json` is generated from
`VADPluginManifest.json_schema()` and is the contract for local integration
package manifests. Manifest `version` values must match semantic version shape
`MAJOR.MINOR.PATCH` with optional prerelease/build suffix as allowed by the
schema.

`schemas/eip.schema.json` is the current Execution Intent Packet schema. EIP
documents carry their own `version` field in strict `MAJOR.MINOR.PATCH` form.
Future EIP schema changes must preserve required local proof, tool-permission,
memory, budget, release, and telemetry fields unless the schema major version is
advanced and migration guidance is added.

SQLite schema versions remain governed by the `ServerStore` migration contract
and `PRAGMA user_version`. New migrations must be forward-only, deterministic,
and covered by store migration tests.

## Compatibility Gates

Before changing a package or schema version:

- Update the source-of-truth version in code or schema, not only docs.
- Keep local-only defaults: no cloud endpoint, live credential, or auto-publish
  behavior may appear in a minor or patch release.
- Preserve `vad mcp run`, `vad control-plane serve`, and `vad local-os demo`
  compatibility for minor and patch releases.
- Keep generated client packages dry-run/reviewable by default, with high-risk tools not approved by default.
- Add or update focused tests for the changed package/schema and run the default
  Docker gate.

Breaking changes require a major version bump, migration notes, and explicit
operator-facing compatibility documentation before publication review.

Publication target decisions are recorded in `docs/publication-decisions.md`.
Those records still require explicit human approval before any package upload,
marketplace submission, or public release artifact distribution.

Local setup and manual per-client fallbacks are documented in
`docs/local-install.md`.
