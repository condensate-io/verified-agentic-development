# Verified Agentic Development (VAD) Agent Invariants & Governance

This repository adheres to the **Verified Agentic Development (VAD)** framework. All autonomous agents (Claude Code, Antigravity, OpenAI Codex, OpenCode, Cursor, Windsurf) operating in this repository must strictly obey the following behavioral and architectural invariants.

---

## 1. Builder-Verifier Separation
- **No Self-Approval**: An agent acting in a builder or coding role must never self-approve, sign off, or merge its own changes.
- **Independent Verification**: Every code modification must be verified by an independent verifier persona (`adversarial-verifier` or `intent-steward`) or passing test suite before promotion.

---

## 2. Orchestrator State & Self-Reporting Prohibitions
- **Restricted State Transitions**: Agents can **never** self-report or transition task states to `pushing`, `mr_created`, or `done`.
- **Human Push Gate**: Git pushes must be verified through the push gate (`git ls-remote` and `git reflog` validation). The `confirm_push` action is strictly reserved for human operators via the UI and is **never** exposed as an MCP tool.
- **Investigation Run Kind**: Diagnostic and triage runs require human approval (`approve_rca`) before reaching closure.

---

## 3. Verification Ladder Discipline (`/verify`)
Before declaring code ready, execute verification steps in strictly cost-ordered sequence (fail-fast):
1. **Lint** (Fast static syntax check)
2. **Types** (`mypy` / type checking)
3. **Scoped Tests** (Targeted unit tests for modified components)
4. **Registry & Integration Tests** (Integrity audits and full component checks)
5. **Model Review** (MEES-scaled adversarial review)

Any failure halts the ladder immediately. Passing runs produce a SHA-256 stamped `VerificationReceipt`.

---

## 4. Review Depth & Minimal Effort Score (MEES)
Review depth is dynamically determined by the Minimal Effort Engineering Score (MEES):
- **MEES $\ge$ 70**: Solo automated verifier pass.
- **MEES 50–69**: Domain agent review required (warn tier).
- **MEES < 50**: Deny tier. Requires domain agent review **plus** the adversarial verifier.
- Minimize churn: Keep changed files within declared budget and avoid adding new dependencies without explicit approval.

---

## 5. Pre-Tool Safety & Privacy Boundaries
- **Credential Guard**: Never generate, log, or edit files containing API tokens, private keys, or passwords.
- **Read Privacy**: Never read `.env*`, `*.pem`, or `id_rsa*` credential files unless explicitly overridden with `READ_PRIVACY_GUARD=off`.
- **Destructive Bash Guard**: Never execute destructive commands (`rm -rf /`, `mkfs`, `dd`, `git push --force`). Commands in the ask-tier require explicit human confirmation.
- **EIP Readiness**: Never begin task implementation unless the referenced Executable Intent Package has `status: ready` and zero open questions.

---

## 6. Execution Memory & Skill Ledger
- Historical skill execution successes and failures are tracked in `vad.memory.SkillLedger`.
- When a skill has an established failure rate $\ge 50\%$, exercise extreme caution and consult recorded failure snippets before retrying.
