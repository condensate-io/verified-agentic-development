from enum import Enum
from pathlib import Path
import subprocess

from pydantic import BaseModel, Field


class VcsType(str, Enum):
    GIT = "git"


class DirtyState(str, Enum):
    CLEAN = "clean"
    DIRTY = "dirty"
    UNKNOWN = "unknown"


class ManifestFile(BaseModel):
    path: str
    kind: str


class RepositoryIntake(BaseModel):
    root: str
    vcs_type: VcsType
    default_branch: str | None = None
    current_branch: str | None = None
    base_revision: str | None = None
    dirty_state: DirtyState
    language_hints: list[str] = Field(default_factory=list)
    manifest_files: list[ManifestFile] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class RepositoryIntakeError(ValueError):
    pass


def assess_repository(path: str | Path, runner=None) -> RepositoryIntake:
    root = Path(path).resolve()
    if not root.exists() or not root.is_dir():
        raise RepositoryIntakeError(f"Repository path does not exist or is not a directory: {root}")

    git_dir = root / ".git"
    if not git_dir.exists():
        if (root / ".hg").exists() or (root / ".svn").exists():
            raise RepositoryIntakeError("Unsupported VCS: only git repositories are supported")
        raise RepositoryIntakeError(f"No supported VCS metadata found at {root}")
    if not git_dir.is_dir():
        raise RepositoryIntakeError("Unsupported git layout: .git file/worktree links are not supported yet")

    current_branch = _read_current_branch(git_dir)
    default_branch = _default_branch(git_dir, current_branch)
    base_revision = _git_stdout(root, ["git", "rev-parse", "HEAD"], runner)
    dirty_state, warnings = _dirty_state(root, runner)
    manifests = _manifest_files(root)

    return RepositoryIntake(
        root=str(root),
        vcs_type=VcsType.GIT,
        default_branch=default_branch,
        current_branch=current_branch,
        base_revision=base_revision,
        dirty_state=dirty_state,
        language_hints=_language_hints(root, manifests),
        manifest_files=manifests,
        warnings=warnings,
    )


def _read_current_branch(git_dir: Path) -> str | None:
    head = git_dir / "HEAD"
    if not head.exists():
        return None
    text = head.read_text(encoding="utf-8").strip()
    prefix = "ref: refs/heads/"
    if text.startswith(prefix):
        return text[len(prefix):]
    return None


def _default_branch(git_dir: Path, current_branch: str | None) -> str | None:
    for candidate in ("main", "master", "trunk"):
        if (git_dir / "refs" / "heads" / candidate).exists():
            return candidate
    return current_branch


def _dirty_state(root: Path, runner=None) -> tuple[DirtyState, list[str]]:
    status = _git_stdout(root, ["git", "status", "--porcelain"], runner, allow_unavailable=True)
    if status is None:
        return DirtyState.UNKNOWN, ["git status unavailable; dirty state unknown"]
    return (DirtyState.DIRTY if status.strip() else DirtyState.CLEAN), []


def _git_stdout(root: Path, command: list[str], runner=None, allow_unavailable: bool = True) -> str | None:
    runner = runner or subprocess.run
    try:
        result = runner(command, cwd=str(root), capture_output=True, text=True, check=False)
    except FileNotFoundError:
        if allow_unavailable:
            return None
        raise RepositoryIntakeError("git executable unavailable")
    if result.returncode != 0:
        if allow_unavailable:
            return None
        raise RepositoryIntakeError(f"git command failed: {' '.join(command)}")
    return result.stdout.strip()


def _manifest_files(root: Path) -> list[ManifestFile]:
    manifests = []
    for relative, kind in _MANIFESTS.items():
        if (root / relative).exists():
            manifests.append(ManifestFile(path=relative, kind=kind))
    return manifests


def _language_hints(root: Path, manifests: list[ManifestFile]) -> list[str]:
    hints = {manifest.kind for manifest in manifests}
    suffix_map = {
        ".py": "python",
        ".js": "javascript",
        ".ts": "typescript",
        ".go": "go",
        ".rs": "rust",
    }
    for file_path in root.rglob("*"):
        if ".git" in file_path.parts or not file_path.is_file():
            continue
        hint = suffix_map.get(file_path.suffix)
        if hint:
            hints.add(hint)
    return sorted(hints)


_MANIFESTS = {
    "pyproject.toml": "python",
    "requirements.txt": "python",
    "package.json": "javascript",
    "go.mod": "go",
    "Cargo.toml": "rust",
}
