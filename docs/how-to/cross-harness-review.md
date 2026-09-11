# How-To: Implement Cross-Harness Review and DSSE Attestation

To guarantee zero blind-spots and prevent self-approval hallucinations, VAD enforces **Cross-Harness Review** paired with cryptographic **DSSE (Dead Simple Signing Envelope)** attestations.

---

## 1. Principles of Cross-Harness Review

1. **Separation of Concerns:** An agent that authors code (`builder`) cannot approve that code for promotion (`verifier`).
2. **Harness Diversity:** A builder running Claude Code is paired with an independent verifier running Cursor, Copilot, or Codex to ensure divergent model biases.
3. **Rebase Stability:** Before review attestation, the patch is checked for semantic rebase drift against `main`.
4. **DSSE Signing:** The review decision is signed into an in-toto compliant DSSE envelope using public key cryptography.

---

## 2. Pairing Builder and Verifier

```python
from vad.review.cross_harness import CrossHarnessCoordinator

coordinator = CrossHarnessCoordinator()

# Assign verifier distinct from builder
pairing = coordinator.create_review_pairing(
    run_id="run-4521",
    builder_harness="claude_code",
    changed_files=["vad/policy/quality_gates.py"]
)

print(f"Assigned Reviewer Harness: {pairing.reviewer_harness}")
# Output: "cursor" or "copilot" (never "claude_code")
```

---

## 3. Detecting Semantic Rebase Conflicts

Before attestation, `RebaseGuard` verifies that branch rebase or merge does not introduce silent semantic drift:

```python
from vad.review.rebase_guard import RebaseGuard

guard = RebaseGuard(repo_path=".")
report = guard.check_rebase_stability(
    feature_branch="feature/dsse-attestation",
    target_branch="main"
)

if not report.is_stable:
    print(f"Rebase Warning: Semantic conflict detected in {report.conflicting_symbols}")
```

---

## 4. Generating and Verifying DSSE Attestations

```python
from vad.review.dsse import DSSEAttestor
from vad.signing.local import LocalDevelopmentSigner

signer = LocalDevelopmentSigner("key-release-guardian-1", b"enterprise-secret-key-32b")
attestor = DSSEAttestor(signer)

# Generate signed envelope
envelope = attestor.sign_review_decision(
    run_id="run-4521",
    builder="claude-code-agent",
    verifier="cursor-verifier-agent",
    decision="passed",
    evidence_digest="a" * 64
)

# Verify envelope in CI
is_valid = attestor.verify_envelope(envelope)
assert is_valid is True
```
