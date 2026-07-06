# VAD CLI Reference

The `vad` command is the reference interface for local VAD workflows.

## Ask

### `vad ask assess <file> [--out <path>] [--json]`

Assess a software engineering ask from a text file.

- `<file>`: ask text file.
- `--out`: write JSON assessment to a file.
- `--json`: print JSON assessment instead of the readable summary.

## EIP

### `vad eip init --name <name> [--out <path>] [--from-assessment <path>] [--force]`

Create a valid EIP template.

- `--name`: required EIP name.
- `--out`: output path, default `eip.yaml`.
- `--from-assessment`: build the EIP from `vad ask assess` JSON.
- `--force`: overwrite an existing output file.

### `vad eip validate <file>`

Validate an EIP YAML or JSON file against the Pydantic contract.

### `vad eip normalize <file> [--out <path>] [--json] [--force]`

Emit a deterministic normalized EIP.

- `--out`: write output to a file.
- `--json`: emit JSON instead of YAML.
- `--force`: overwrite an existing output file.

### `vad eip diff <old-file> <new-file> [--json]`

Compare two valid EIPs.

- `--json`: emit machine-readable changes.

### `vad eip retro <file>`

Run retrospective analysis on an evidence bundle.

## Proof

### `vad proof map <file> [--out <path>] [--force]`

Map EIP proof obligations to a proof plan.

- `<file>`: EIP file.
- `--out`: write proof plan YAML.
- `--force`: overwrite an existing output file.

## Loop

### `vad loop run <eip-file> <proof-plan-file> [--builder <id>] [--verifier <id>] [--out <path>]`

Run the bounded VAD orchestrator.

- `--builder`: builder identity, default `builder`.
- `--verifier`: verifier identity, default `verifier`.
- `--out`: write run result JSON.

The command exits nonzero unless the final decision is `passed`.

## Evidence

### `vad evidence inspect <file>`

Inspect typed run evidence and verify an included evidence hash when present.

## Effort

### `vad effort score --type <type> [--before <rev>] [--after <rev|WORKTREE>] [--json] [--readable] [--warn-only]`

Score a change with MEES.

- `--type`: required effort type: `bugfix`, `feature`, `refactor`, `greenfield`, `test`, or `migration`.
- `--before`: Git base revision, default `HEAD`.
- `--after`: Git target revision or `WORKTREE`, default `WORKTREE`.
- `--json`: emit JSON output.
- `--readable`: emit a compact readable summary.
- `--warn-only`: do not exit nonzero for warn/block policy results.

Exit codes:

- `0`: pass, or warn/block with `--warn-only`.
- `1`: block or invalid input.
- `2`: warn.

## MCP

### `vad mcp run`

Run the VAD MCP stdio server.

### `vad mcp install [claude|cursor]`

Install or print MCP configuration for Claude Code or Cursor.

VSCode MCP-capable extensions, Windsurf, OpenCode, and generic MCP clients can use the manual stdio configuration printed by `vad mcp install` or run `vad mcp run` directly.

Manual MCP configuration must not include API keys, signing secrets, deployment credentials, or approval tokens.
