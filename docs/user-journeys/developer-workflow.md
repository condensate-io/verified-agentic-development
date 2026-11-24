# User Journey: Developer & AI Pair Programming with Governed AI

This guide documents the end-to-end developer experience using Verified Agentic Development (VAD) Enterprise. It walks an engineer through the daily lifecycle of using an AI coding assistant (Claude Code, Cursor, GitHub Copilot, or Codex) under real-time governance, automated testing, and cryptographic verification.

---

## 1. Journey Overview & Lifecycle Stages

```
   Stage 1: Session Initiation & Zero-Trust Integrity Check (SkillGuard)
                               │
                               ▼
   Stage 2: Intent Formulation & Executable Intent Package (EIP)
                               │
                               ▼
   Stage 3: Governed Pair Programming (The 7 Lifecycle Hooks)
                               │
                               ▼
   Stage 4: Local Verification in Disposable Docker on WSL
                               │
                               ▼
   Stage 5: Cross-Harness Review & DSSE Attestation
                               │
                               ▼
   Stage 6: Staged Commit & Release Promotion
```

---

## 2. Stage 1: Morning Session Initiation & Zero-Trust Check

Every development session begins with automated verification of the engineer's environment to guarantee that no unauthorized skills, tampered prompt templates, or shadow tools are loaded.

### How It Works
When the developer launches their terminal or IDE:
1. The **`SessionStart` hook** triggers automatically.
2. VAD's **SkillGuard** integrity scanner traverses:
   - Global rules (`~/.claude/rules/*.md`)
   - Workspace rules (`.cursorrules`, `.github/copilot-instructions.md`)
   - Custom slash command definitions and hooks
3. SHA-256 cryptographic digests of all loaded components are compared against the central **`schemas/integrity_registry.json`**.

### CLI Invocations
```bash
# Verify workspace integrity manually
vad integrity check

# Output:
# [INFO] Checking workspace integrity against SkillGuard registry...
# [PASS] 14 skills verified (SHA-256 matches)
# [PASS] 7 lifecycle hooks verified
# [PASS] 4 slash commands verified
# [OK] Workspace is trusted (Zero-Trust Integrity Verified).
```

If an unauthorized modification is detected:
```bash
# [FAIL] Tampered skill detected: ~/.claude/skills/deploy.md
# [FAIL] Hash mismatch: expected a8f2b1..., observed 91c4e2...
# [BLOCK] Session start aborted. Contact your security administrator.
```

---

## 3. Stage 2: Intent Formulation & EIP Drafting

Rather than unstructured natural language prompting, VAD anchors changes in an **Executable Intent Package (EIP)**. The EIP establishes unambiguous boundaries, invariants, and acceptance criteria.

### Creating the EIP
```bash
vad eip init --name saml-sso-integration --out eip.yaml
```

### Risk, Assurance, and Freshness
Every EIP carries a **risk tier** (`low`, `medium`, `high`, `critical`). The tier decides how much proof is required and how much autonomy the change earns:

| Risk tier | Default assurance | Evidence stays fresh for | Who must be involved |
|-----------|-------------------|--------------------------|----------------------|
| `low` | `A1_BASIC` | 24 hours | Checks only |
| `medium` | `A2_INDEPENDENT` | 4 hours | An independent verifier |
| `high` | `A3_ATTESTED` | 1 hour | Independent verifier plus a human release guardian |
| `critical` | `A4_RESTRICTED` | 15 minutes | Explicit authorization, independent verifier, two human approvers |

You can set `assurance_level` and `freshness_policy` explicitly in the EIP. A `critical` EIP cannot be set below `A4_RESTRICTED`. Individual proof obligations may also require independent execution (`independent_execution_required`). A SAML sign-in change like the example below would normally be `high` risk.

If a memory provider is configured, VAD may add advice when you plan the change: relevant past incidents, and regression proof obligations from lessons a human has approved. Unapproved lessons are never applied.

### Anatomical Structure of `eip.yaml`
```yaml
apiVersion: vad.io/v1alpha1
kind: ExecutableIntentPackage
metadata:
  name: saml-sso-integration
  author: engineer@company.com
  created_at: "2026-09-11T09:00:00Z"
spec:
  problem_statement: >
    Enterprise customers require SAML 2.0 Identity Provider (IdP) authentication
    alongside existing username/password logins.
  
  scope:
    allowed_directories:
      - "vad/deploy/sso_saml.py"
      - "tests/test_sso_saml.py"
    forbidden_directories:
      - "vad/server/db/"
      - ".github/workflows/"
      
  invariants:
    - id: INV-01
      description: "Existing local credentials authentication must remain unaffected."
    - id: INV-02
      description: "SAML assertions must validate X.509 signatures and check clock skew."
    - id: INV-03
      description: "No external non-loopback network calls during unit tests."
      
  proof_obligations:
    - id: PROOF-01
      kind: automated_test
      command: "pytest tests/test_sso_saml.py -v"
    - id: PROOF-02
      kind: security_audit
      command: "pytest tests/test_security_audit.py -k test_security_audit_default_tests_only_use_local_network_endpoints"
```

---

## 4. Stage 3: Governed Pair Programming (Lifecycle Hooks in Action)

The developer opens Claude Code or Cursor and instructs the AI to implement the feature according to `eip.yaml`.

### 4.1 `UserPromptSubmit` Hook
Before the prompt reaches the LLM, the hook intercepts the message:
- Validates that the active EIP exists.
- Injects active invariants (`INV-01`, `INV-02`, `INV-03`) into the system prompt.
- Restricts the context window to relevant codebase slices.

### 4.2 `PreToolUse` Hook: Guarding File Edits and Commands
When the AI assistant issues a tool call (e.g., `write_to_file` or `bash.execute`):
1. **Boundary Guard:** Compares the target file path against `spec.scope.allowed_directories`. If the assistant attempts to modify `vad/server/db/store.py`, `PreToolUse` halts the tool call:
   ```json
   {
     "allow": false,
     "decision": "denied",
     "reason": "Scope violation: vad/server/db/store.py is outside allowed scope"
   }
   ```
2. **Action Governance:** Blocks destructive shell commands (`rm -rf`, `curl`, `git push -f`).

### 4.3 `PostToolUse` Hook: Audit & Secret Redaction
After the tool completes:
- Inspects stdout/stderr for accidental secret leakage (`sk-...`, API keys, passwords).
- Automatically redacts sensitive tokens before displaying them in the chat UI.
- Appends the event to the tamper-evident audit ledger (`vad/audit/local_audit.py`).

### 4.4 `Stop` Hook: Definition of Done (DoD) Gate
When the assistant claims the task is complete, the `Stop` hook blocks termination until:
- All files listed in `spec.scope` have been created or modified.
- All unit tests have been executed.

---

## 5. Stage 4: Local Verification in Disposable Docker on WSL

Under VAD Enterprise constraints, testing must never pollute the developer's host machine. All execution occurs inside a disposable Docker container on WSL (`vad-runner:3.11`).

### Running the Verification Loop
The developer types:
```bash
/vad-verify
```
Or triggers it via terminal:
```bash
wsl docker run --rm -v /mnt/c/LocalProjects/condensate:/workspace -w /workspace -e PYTHONPATH=/workspace vad-runner:3.11 pytest tests/test_sso_saml.py -v
```

### Verification Outcomes
1. **Unit Test Execution:** Runs all test cases defined in `tests/test_sso_saml.py`.
2. **Cryptographic Proof Bundle:** Generates `evidence/I2.json` recording:
   - Test execution exit code (0)
   - SHA-256 hashes of changed source files
   - Token consumption & duration
   - Timestamp and runner ID

### Your Own Test Run Is Not the Final Proof
For `medium` risk and above, the result of the builder's own test run is treated as a **claim**, not proof. VAD runs the proof obligations again through an **independent verifier** (a different actor, in an isolated environment) against the exact commit and tree. The verification receipt records the commit, tree hash, environment digest, verifier identity, and an expiry time.

If you edit, rebase, or change dependencies after verification, the receipt becomes **stale** and release is blocked until verification runs again. This is expected behavior, not an error in your change.

To run the whole governed pipeline (EIP, proof plan, fenced build, independent verification, policy decision, release record, audit check) in one go:
```bash
vad workflow verified-delivery --run-id run-001 --eip-name saml-sso-integration \
  --request "Add SAML sign-in" --risk high --approver release-guardian-alex
```
See [Verified Delivery Flow](verified-delivery.md) for what each risk tier needs.

---

## 6. Stage 5: Cross-Harness Review & DSSE Attestation

To eliminate self-approval hallucinations, an independent AI verifier reviews the patch before it can be merged.

### Initiating Review
```bash
vad review request --run-id current --builder claude_code
```
- The **Cross-Harness Coordinator** pairs the patch with a verifier harness (e.g. Cursor or Codex).
- **RebaseGuard** confirms the patch has zero semantic conflicts with `main`.
- The verifier reviews the diff against the EIP acceptance criteria.
- Upon passing, the review outcome is wrapped in a **DSSE (Dead Simple Signing Envelope)** signed with a local **Ed25519** key (generated under `.vad/keys/` on first use; there is no shared default secret in strict mode). The attestation records the EIP digest, proof plan digest, assurance level, and expiry:
  ```json
  {
    "payloadType": "application/vnd.in-toto+json",
    "payload": "eyJfdHlwZSI6ICJodHRwczovL2luLXRvdG8uaW8vU3RhdGVtZW50L3YwLjEiLCAic3ViamVjdCI6IFt7Im5hbWUiOiAidmFkL2RlcGxveS9zc29fc2FtbC5weSIsICJkaWdlc3QiOiB7InNoYTI1NiI6ICJiNGY3Li4uIn19XX0=",
    "signatures": [
      {
        "keyid": "key-verifier-cursor",
        "sig": "MEUCIQDK98fL..."
      }
    ]
  }
  ```

---

## 7. Stage 6: Staged Commit & PR Creation

With all proof obligations satisfied and the DSSE attestation signed:
```bash
vad commit --eip eip.yaml --evidence evidence/I2.json
```
- Commits changes to the local feature branch without touching other workspaces.
- Attaches the DSSE envelope as a Git note (`refs/notes/vad-attestations`).
- The developer opens the pull request, where CI confirms the signature before merge.

---

## 8. Common Developer Troubleshooting Scenarios

| Issue Encountered | Root Cause | Immediate Resolution |
|-------------------|------------|----------------------|
| **`PreToolUse: Scope violation`** | AI tried to edit a file outside `eip.yaml` | Add file to `spec.scope` or instruct AI to refactor within assigned modules. |
| **`PreToolUse: Action denied`** | Prohibited command (`pip install`, `curl`) | Run dependency installations inside Docker runner rather than locally. |
| **`SecurityAudit: Non-local URL`** | Test contains non-loopback URL | Replace with `http://127.0.0.1:<port>` or `localhost`. |
| **`Stop: Incomplete obligations`** | Proof plan tests haven't been run | Execute `/vad-verify` inside Docker before ending the session. |
| **`blocked_stale_evidence`** | Code, tree, dependencies, or time window changed after verification | Re-run independent verification on the current commit. |
| **`human_approval_required`** | High risk change has no distinct human guardian yet | Ask a release guardian (not the builder) to approve. |
| **`denied_policy` (self-verification)** | Builder and verifier are the same actor | Use a distinct verifier identity. |
| **`409 Conflict` on a work item update** | Your host holds an old `fence_token` after the task was reassigned | Re-claim the work item to receive the current token. |

After a successful run, you can check the trail yourself with `vad audit verify-trail <run_id>`.
