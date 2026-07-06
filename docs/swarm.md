# Swarm Operations

VAD swarm support is a local deterministic coordinator for role-separated demonstrators. It is not a production distributed agent fleet.

## Local Swarm Run

```bash
vad swarm run --run-id level3-demo-swarm --fixture examples/level3-demo --workdir /tmp/vad-swarm-work --state swarm-state.json --out swarm-run.json
```

When `--fixture` and `--workdir` are supplied, the command:

- copies the fixture repository before making changes;
- runs planner, builder, verifier, and auditor roles through the local coordinator;
- writes a deterministic build artifact into the copied repo;
- runs fixture tests as proof;
- records messages, completed task ids, modified files, and agent-role evidence.

Without `--fixture`, `vad swarm run` executes the smaller built-in local task graph used by unit tests.

## Status Inspection

```bash
vad swarm status swarm-state.json --out swarm-status.json
```

Status reconstructs task graph state, messages, and final decision from the persisted local state file.

## Operator Boundaries

- Use `--workdir` outside the source fixture when running demos.
- Treat the local coordinator as a replayable demonstrator, not a distributed scheduler.
- Do not assume independent coding-system clients have executed unless their activity appears in evidence/dashboard records.
- Keep planner, builder, verifier, auditor, and release-guardian duties separated in evidence.

## Command Coverage

- Tested: `vad swarm run`, `vad swarm run --fixture ... --workdir ...`, and `vad swarm status`.
- Illustrative: `/tmp/vad-swarm-work` and output filenames are local examples only.
