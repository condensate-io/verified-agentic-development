"""Cursor Projector Target: Renders canonical governance specs into .cursor/rules/*.mdc and AGENTS.md."""

from typing import Dict, List, Optional
from vad.harness.targets.claude_code_target import CanonicalHarnessSpec, CanonicalAgent, GovernanceRule


class CursorTarget:
    """Projects canonical governance rules into Cursor IDE MDC rules and AGENTS.md."""

    def render(self, spec: CanonicalHarnessSpec) -> Dict[str, str]:
        outputs: Dict[str, str] = {}

        # 1. Main AGENTS.md
        agents_md_lines = [
            f"# AGENTS.md — {spec.project_name}",
            f"> {spec.description}",
            "",
            "## Cursor Agent Definitions & Operational Directives",
            "",
        ]
        for agent in spec.agents:
            agents_md_lines.extend([
                f"### Agent: {agent.name}",
                f"- **Role**: {agent.role}",
                f"- **Allowed Tools**: `[{', '.join(agent.allowed_tools) or 'default'}]`",
                f"- **Bound Rules**: `[{', '.join(agent.rules) or 'all'}]`",
                "",
            ])
        outputs["AGENTS.md"] = "\n".join(agents_md_lines)

        # 2. Main Cursor MDC Rule (.cursor/rules/vad_governance.mdc)
        main_rule_lines = [
            "---",
            f"description: Enterprise VAD Governance Directives for {spec.project_name}",
            'globs: ["*"]',
            "alwaysApply: true",
            "---",
            "",
            "# VAD Governance Directives",
            "",
            "## Core Invariants",
            "- Strict container isolation: All test executions must occur in disposable Docker containers inside WSL.",
            "- Zero direct push: Autonomous commits must be staged; git push requires explicit human operator confirmation.",
            "- Proof obligations: Every functional change requires verifiable evidence output in `evidence/<task-id>.json`.",
            "",
            "## Enforced Rules",
        ]
        for rule in spec.rules:
            main_rule_lines.append(f"- **{rule.id} ({rule.name})**: {rule.description}")

        outputs[".cursor/rules/vad_governance.mdc"] = "\n".join(main_rule_lines)

        # 3. Individual rule files
        for rule in spec.rules:
            slug = rule.id.lower().replace("_", "-")
            rule_file = f".cursor/rules/{slug}.mdc"
            rule_content = [
                "---",
                f"description: Governance Rule {rule.name}",
                'globs: ["*"]',
                "alwaysApply: false",
                "---",
                "",
                f"# Rule: {rule.name} ({rule.id})",
                f"Severity: `{rule.severity}`",
                "",
                rule.description,
            ]
            outputs[rule_file] = "\n".join(rule_content)

        return outputs
