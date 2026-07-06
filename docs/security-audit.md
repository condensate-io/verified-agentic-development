# Security Audit

This audit records the current hardening checks for the local Level 3/4 reference implementation. It is intentionally scoped to default, offline behavior: fake providers, local fixtures, local SQLite state, stdlib HTTP serving, and deterministic Docker tests.

## Controls Checked

- Filesystem writes are guarded by workspace containment before mutation. Patch application validates target paths through `WorkspaceSandbox` and fails before writing when a diff attempts to escape the repository root.
- Evidence fixtures do not contain committed secret values. The Level 3 demo fixture is scanned for inline API keys, password assignments, private-key markers, token assignments, and provider key patterns.
- Default tests avoid live network calls. Tests may use loopback HTTP servers for local UI/API smoke coverage; non-local HTTP hosts and network primitives outside the local allowlist fail the audit test.
- UI actions do not bypass backend policy. The browser-facing approval endpoint posts through the same backend approval service and denies builder self-approval with a persisted policy-shaped denial.
- Production deployment apply requires an approval reference. Dry runs and staging applies can remain local, but production apply fails closed without approval, telemetry, and rollback capability.

## Out Of Scope

- Live cloud provider, paid model, KMS, or production deployment calls are not enabled by default, and no live credentials are required.
- The local HMAC signer is for development evidence only and is not a production key-management design.
- The hosted UI is a local dashboard/control surface over backend APIs; it is not an independent authorization boundary.

## Verification

Run the focused audit gate:

```powershell
wsl -e bash -lc "cd /mnt/c/LocalProjects/condensate-arch-docs/verified-agentic-development && docker build -t vad-test:local . && docker run --rm vad-test:local python -m pytest tests/test_security_audit.py"
```

Run the default completion gate:

```powershell
wsl -e bash -lc "cd /mnt/c/LocalProjects/condensate-arch-docs/verified-agentic-development && docker build -t vad-test:local . && docker run --rm vad-test:local"
```
