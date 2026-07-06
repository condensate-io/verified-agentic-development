# Repository Automation

Repository automation is the local path for applying bounded changes to a fixture or target repository while preserving VAD evidence.

## Intake

`vad repo assess <path>` is read-only and records:

- repository root;
- VCS type;
- default and current branch;
- base revision when Git is available;
- dirty-state signal;
- language hints;
- dependency and build manifest files.

Unsupported VCS types fail closed. Git worktree link files are not writable automation targets yet.

## Patch Application

```bash
vad repo patch <repo> <eip-file> <proof-plan-file> --patch <patch-file> --out patch-evidence.json
```

The patch command:

- rejects dirty Git worktrees unless `--allow-dirty` is supplied;
- checks the patch stays inside EIP scope boundaries;
- blocks dependency manifest changes unless `--approve-dependencies` is supplied;
- runs mapped proof commands;
- rolls back the patch when proof fails;
- records patch digest, changed files, rollback state, and verification evidence.

## Ask-To-Run Flow

```bash
vad repo run <repo> <ask-file> --patch <patch-file> --out run-evidence.json
```

The run command assesses the ask, builds an EIP/proof plan from local signals, applies the supplied patch, runs proofs, and emits a pass or blocked decision. It does not synthesize patches by itself; the patch file remains an explicit input.

## Operator Boundaries

- Use disposable fixture repos for demonstrators.
- Keep live credentials out of repo files and patch inputs.
- Treat dependency manifest changes as approval-sensitive.
- Do not use `--allow-dirty` or `--approve-dependencies` in unattended flows unless a higher-level policy has already approved that risk.

## Command Coverage

- Tested: `vad repo assess`, `vad repo patch`, and `vad repo run`.
- Illustrative: commands using placeholder paths such as `<repo>` or `<patch-file>` show shape only and must be replaced with local fixture paths.
