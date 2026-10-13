# GitHub Copilot Instructions - Verified Agentic Development (VAD)

Welcome to the **Verified Agentic Development** repository. When reviewing pull requests, generating code, or answering questions in this codebase, adhere to the following governance requirements:

## Code Generation & Modification Standards
- **Minimal Complexity Delta**: Strive for the smallest, cleanest change that satisfies requirements. Keep MEES (Minimal Effort Engineering Score) $\ge 70$.
- **Dependency Hygiene**: Avoid adding new dependencies to `pyproject.toml` unless strictly necessary and approved.
- **Fail-Fast Verification**: Always run tests and type checking in order of ascending cost (lint -> types -> unit tests -> integration tests).

## Security & Privacy Invariants
- **Secret Protection**: Block emission of sensitive credentials, tokens, or API keys in code or commit messages.
- **Privacy Boundaries**: Never read or emit content from `.env*`, `*.pem`, or private SSH keys.
- **Safe Commands**: Do not suggest destructive shell commands (`rm -rf /`, `mkfs`, force pushes).

## Verification & Workflow Roles
- **Builder vs Verifier**: Differentiate clearly between code generation (builder) and independent review (adversarial-verifier / intent-steward).
- **No Self-Promoting Runs**: Agents do not self-report task completion (`pushing`, `mr_created`, `done`). Push confirmation must be approved by human operators via the UI.
