from pathlib import Path

from pydantic import BaseModel

from vad.guards.filesystem import FilesystemDecision, FilesystemGuard


class WorkspaceSandbox(BaseModel):
    root: str
    writable: bool = False

    @classmethod
    def from_path(cls, root: str | Path, writable: bool = False) -> "WorkspaceSandbox":
        return cls(root=str(Path(root).resolve()), writable=writable)

    def resolve_read(self, relative_path: str | Path) -> FilesystemDecision:
        return FilesystemGuard(self.root, writable=self.writable).resolve_read(relative_path)

    def resolve_write(self, relative_path: str | Path) -> FilesystemDecision:
        return FilesystemGuard(self.root, writable=self.writable).resolve_write(relative_path)
