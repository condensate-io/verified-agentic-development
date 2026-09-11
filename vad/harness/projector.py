"""Multi-Harness Projector: Projects canonical governance specifications into Claude Code, Cursor, and Copilot configs."""

import argparse
from pathlib import Path
import sys
from typing import Dict, List, Optional
import yaml

from vad.harness.targets.claude_code_target import (
    CanonicalAgent,
    CanonicalHarnessSpec,
    ClaudeCodeTarget,
    GovernanceRule,
)
from vad.harness.targets.cursor_target import CursorTarget
from vad.harness.targets.copilot_target import CopilotTarget


class MultiHarnessProjector:
    """Orchestrates projection of single-source-of-truth governance to multiple AI coding harnesses."""

    def __init__(self):
        self.targets = {
            "claude": ClaudeCodeTarget(),
            "cursor": CursorTarget(),
            "copilot": CopilotTarget(),
        }

    def load_spec_from_yaml(self, path: Path) -> CanonicalHarnessSpec:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        return CanonicalHarnessSpec(**data)

    def project(
        self,
        spec: CanonicalHarnessSpec,
        target_name: str = "all",
        output_root: Path = Path("."),
    ) -> Dict[str, Path]:
        target_key = target_name.lower().strip()
        selected_renderers = []

        if target_key == "all":
            selected_renderers = list(self.targets.values())
        elif target_key in self.targets:
            selected_renderers = [self.targets[target_key]]
        else:
            raise ValueError(f"Unknown harness target '{target_name}'. Supported: all, claude, cursor, copilot")

        written_files: Dict[str, Path] = {}
        for renderer in selected_renderers:
            rendered = renderer.render(spec)
            for rel_path, content in rendered.items():
                out_path = output_root / rel_path
                out_path.parent.mkdir(parents=True, exist_ok=True)
                out_path.write_text(content, encoding="utf-8")
                written_files[rel_path] = out_path

        return written_files


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description="Project canonical VAD governance to AI harnesses")
    parser.add_argument("--target", default="all", choices=["all", "claude", "cursor", "copilot"], help="Harness target")
    parser.add_argument("--spec", default="vad_spec.yaml", help="Path to canonical harness spec YAML")
    parser.add_argument("--output", default=".", help="Root directory to output projected configurations")
    args = parser.parse_args(argv)

    spec_path = Path(args.spec)
    if not spec_path.exists():
        # Fallback default spec
        spec = CanonicalHarnessSpec(
            project_name="VADProject",
            description="Autonomous verified agentic development",
            agents=[CanonicalAgent(name="vad-builder", role="Core Builder", allowed_tools=["pytest", "git"])],
            rules=[GovernanceRule(id="R1", name="SafetyBoundary", description="No direct push without approval")],
        )
    else:
        projector = MultiHarnessProjector()
        spec = projector.load_spec_from_yaml(spec_path)

    projector = MultiHarnessProjector()
    written = projector.project(spec, target_name=args.target, output_root=Path(args.output))
    print(f"Successfully projected {len(written)} files for target '{args.target}'.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
