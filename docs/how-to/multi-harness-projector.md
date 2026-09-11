# How-To: Project Universal VAD Rules to Multiple Harnesses

In enterprise environments, different engineers use different AI tools: Claude Code, Cursor, GitHub Copilot, Windsurf, or OpenCode. The **Multi-Harness Projector** compiles a single source of truth into native configurations for all supported harnesses.

---

## 1. The Universal AST / IR

VAD defines a universal Intermediate Representation (IR) for development rules, skills, and coding standards:

```
                          ┌───────────────────────────┐
                          │   Universal Ruleset YAML  │
                          └─────────────┬─────────────┘
                                        │
                                        ▼
                          ┌───────────────────────────┐
                          │   VAD Harness Projector   │
                          └──────┬──────────┬─────────┘
                                 │          │
                 ┌───────────────┘          └────────────────┐
                 ▼                                           ▼
   ┌───────────────────────────┐               ┌───────────────────────────┐
   │        Claude Code        │               │           Cursor          │
   │ (~/.claude/rules/*.md)    │               │      (.cursorrules)       │
   └───────────────────────────┘               └───────────────────────────┘
                 │                                           │
                 └───────────────────────┬───────────────────┘
                                         ▼
                           ┌───────────────────────────┐
                           │       GitHub Copilot      │
                           │ (.github/copilot-inst.md) │
                           └───────────────────────────┘
```

---

## 2. Projecting to Claude Code
```bash
python -m vad.harness.projector --target claude_code --out-dir ~/.claude
```
Emits modular markdown rules in `~/.claude/rules/` and configures hook triggers in `settings.json`.

---

## 3. Projecting to Cursor (.cursorrules)
```bash
python -m vad.harness.projector --target cursor --out-dir .
```
Generates a structured `.cursorrules` file at the root of the workspace enforcing project idioms, prohibited libraries, and code quality standards.

---

## 4. Projecting to GitHub Copilot
```bash
python -m vad.harness.projector --target copilot --out-dir .github
```
Emits `.github/copilot-instructions.md` with explicit prompt guidelines, test verification obligations, and scope constraints.

---

## 5. CI Automation
Add projector check to your GitHub Actions or GitLab CI pipeline to ensure harness rules never diverge from canonical policy:
```yaml
- name: Verify Multi-Harness Sync
  run: |
    python -m vad.harness.projector --verify
```
