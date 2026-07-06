# Deployment Control

VAD deployment support is currently a governed fake-provider lifecycle for planning, dry-run, apply, and rollback evidence. It does not deploy to live cloud or production infrastructure.

## Commands

```bash
vad deploy plan eip.yaml target.yaml --out deployment-plan.yaml
vad deploy dry-run deployment-plan.yaml --out dry-run.json
vad deploy apply deployment-plan.yaml --approval-ref approval:prod-1 --out apply.json
vad deploy rollback apply.json --rollback-approval-ref approval:rollback-1 --out rollback.json
vad deploy demo --fixture examples/level3-demo --out-dir /tmp/vad-deploy-demo --out deploy-demo.json
vad deploy failure-demo --fixture examples/level3-demo --out-dir /tmp/vad-failure-demo --out failure-demo.json
```

Production apply requires approval, telemetry, and rollback. Rollback requires separate rollback approval. Secret values are rejected in target files; use secret references such as `env:NAME`, `vault:path`, or cloud secret-manager references.

The `demo` and `failure-demo` commands are local demonstrators. They use the fixture deployment target, fake provider state, local telemetry decisions, local signing, rollback feedback, and SQLite dashboard evidence. They are not live deployment commands.

Default tests use only the fake provider and disposable containers. Live deployment providers must add separate opt-in tests and must never run by default.

## Command Coverage

- Tested: `vad deploy plan`, `vad deploy dry-run`, `vad deploy apply`, `vad deploy rollback`, `vad deploy demo`, and `vad deploy failure-demo`.
- Illustrative: placeholder files such as `eip.yaml`, `target.yaml`, and `/tmp/vad-deploy-demo` must be replaced with local fixture paths.
- Not implemented: live cloud deployment providers.
