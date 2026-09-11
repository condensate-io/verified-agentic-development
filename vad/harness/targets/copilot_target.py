"""GitHub Copilot Projector Target: Renders canonical governance specs into .github/copilot-instructions.md."""

from typing import Dict, List
from vad.harness.targets.claude_code_target import CanonicalHarnessSpec, CanonicalAgent, GovernanceRule


class CopilotTarget:
    """Projects canonical governance specs into GitHub Copilot instructions."""

    def render(self, spec: CanonicalHarnessSpec) -> Dict[str, str]:
        lines = [
            f"# GitHub Copilot Custom Instructions for {spec.project_name}",
            f"> {spec.description}",
            "",
            "## Verified Agentic Development (VAD) Directives",
            "These instructions guide GitHub Copilot in suggestions, chat completions, and autonomous agent mode.",
            "",
            "### Architectural & Execution Invariants",
            "1. **Container Isolation**: Never execute tests or builds directly on the developer host. All tests must be executed in throwaway Docker containers running inside WSL.",
            "2. **Evidence-Driven Engineering**: Any suggested fix, refactor, or implementation must include a corresponding pytest test module and cryptographic evidence record.",
            "3. **Human Authorization Boundary**: Never perform direct or autonomous `git push`. Changes must be committed to local staging awaiting explicit human operator confirmation.",
            "",
            "### Active Governance Rules",
        ]

        for rule in spec.rules:
            lines.append(f"- **Rule `{rule.id}`** ({rule.name}): {rule.description} [Severity: `{rule.severity}`]")

        if spec.agents:
            lines.extend([
                "",
                "### Designated Roles",
            ])
            for agent in spec.agents:
                lines.append(f"- `{agent.name}`: {agent.role}")

        lines.extend([
            "",
            "### Code Style & Quality Standards",
            "- Use Python 3.11+ type hints, Pydantic v2 models, and strict dataclasses.",
            "- Adhere strictly to the Definition of Ready (DoR) gate before declaring tasks complete.",
        ])

        return {".github/copilot-instructions.md": "\n".join(lines)}
