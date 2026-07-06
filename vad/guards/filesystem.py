from pathlib import Path

from pydantic import BaseModel


class FilesystemDecision(BaseModel):
    allowed: bool
    path: str | None = None
    reason: str


class FilesystemGuard:
    def __init__(self, root: str | Path, writable: bool = False):
        self.root = Path(root).resolve()
        self.writable = writable

    def resolve_read(self, relative_path: str | Path) -> FilesystemDecision:
        return self._resolve(relative_path, write=False)

    def resolve_write(self, relative_path: str | Path) -> FilesystemDecision:
        return self._resolve(relative_path, write=True)

    def _resolve(self, relative_path: str | Path, write: bool) -> FilesystemDecision:
        if write and not self.writable:
            return FilesystemDecision(allowed=False, reason="workspace is read-only")

        raw_path = Path(relative_path)
        if raw_path.is_absolute():
            return FilesystemDecision(allowed=False, reason="absolute paths are not allowed")

        target = (self.root / raw_path)
        resolved = _resolve_for_access(target)
        if not _is_relative_to(resolved, self.root):
            return FilesystemDecision(allowed=False, reason="path escapes workspace root")

        return FilesystemDecision(allowed=True, path=str(resolved), reason="path allowed")


def _resolve_for_access(path: Path) -> Path:
    if path.exists():
        return path.resolve()
    return path.parent.resolve() / path.name


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False
