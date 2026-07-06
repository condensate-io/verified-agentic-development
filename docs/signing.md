# Signing Operations

VAD signing support is local development signing for evidence and deployment attestations. It is intended for deterministic verification and demonstrators, not production key management.

## Evidence Signing

```bash
vad sign evidence evidence.json --key-id local-dev --secret-file secret.key --out signed-evidence.json
vad sign verify signed-evidence.json --secret-file secret.key --out verification.json
```

The signer:

- computes a deterministic payload digest;
- creates an HMAC-SHA256 signature envelope;
- keeps the signing secret out of the signed output;
- rejects tampered payloads during verification.

## Deployment Attestation

`vad deploy demo` signs fake deployment evidence as part of the local demonstrator:

```bash
vad deploy demo --fixture examples/level3-demo --out-dir /tmp/vad-deploy-demo --out deploy-demo.json
```

This uses local signing only. It does not integrate with KMS, Sigstore, hardware-backed keys, or production release signing.

## Operator Boundaries

- Store local development secrets outside committed fixtures.
- Rotate throwaway secrets used in local demonstrations.
- Do not use local HMAC secrets as production signing keys.
- Treat failed verification as a hard blocker for release promotion.

## Command Coverage

- Tested: `vad sign evidence`, `vad sign verify`, and `vad deploy demo`.
- Illustrative: `secret.key`, `/tmp/vad-deploy-demo`, and output filenames are local examples only.
