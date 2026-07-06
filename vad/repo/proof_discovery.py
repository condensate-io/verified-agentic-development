import json
from pathlib import Path

from pydantic import BaseModel, Field


class DiscoveredProofCommand(BaseModel):
    ecosystem: str
    command: list[str]
    reason: str


class ProofDiscovery(BaseModel):
    root: str
    commands: list[DiscoveredProofCommand] = Field(default_factory=list)
    blocker: str | None = None

    @property
    def has_proof_commands(self) -> bool:
        return bool(self.commands)


def discover_proof_commands(path: str | Path) -> ProofDiscovery:
    root = Path(path).resolve()
    commands: list[DiscoveredProofCommand] = []

    if _has_python_tests(root):
        commands.append(DiscoveredProofCommand(
            ecosystem="python",
            command=["pytest"],
            reason="Python test directory or pytest config detected",
        ))

    node_command = _node_test_command(root)
    if node_command:
        commands.append(node_command)

    if (root / "go.mod").exists():
        commands.append(DiscoveredProofCommand(
            ecosystem="go",
            command=["go", "test", "./..."],
            reason="go.mod detected",
        ))

    return ProofDiscovery(
        root=str(root),
        commands=commands,
        blocker=None if commands else "no proof command discovered",
    )


def _has_python_tests(root: Path) -> bool:
    return any((root / name).exists() for name in ("pytest.ini", "tox.ini")) or (root / "tests").is_dir()


def _node_test_command(root: Path) -> DiscoveredProofCommand | None:
    package_json = root / "package.json"
    if not package_json.exists():
        return None
    try:
        payload = json.loads(package_json.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    scripts = payload.get("scripts", {})
    if isinstance(scripts, dict) and scripts.get("test"):
        return DiscoveredProofCommand(
            ecosystem="node",
            command=["npm", "test"],
            reason="package.json test script detected",
        )
    return None
