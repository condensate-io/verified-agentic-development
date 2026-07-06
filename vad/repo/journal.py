import hashlib
from pathlib import Path

from pydantic import BaseModel, Field

from vad.evidence.bundle import PatchJournalEvidence


class FileSnapshot(BaseModel):
    path: str
    existed: bool
    content: bytes | None = None


class RollbackResult(BaseModel):
    rolled_back: bool
    restored_files: list[str] = Field(default_factory=list)
    blocker: str | None = None


class PatchJournal(BaseModel):
    root: str
    snapshots: list[FileSnapshot] = Field(default_factory=list)

    @classmethod
    def capture(cls, root: str | Path, relative_paths: list[str]) -> "PatchJournal":
        resolved_root = Path(root).resolve()
        snapshots = []
        for relative_path in relative_paths:
            target = (resolved_root / relative_path).resolve()
            if not _is_relative_to(target, resolved_root):
                raise ValueError(f"path escapes repository root: {relative_path}")
            snapshots.append(FileSnapshot(
                path=relative_path,
                existed=target.exists(),
                content=target.read_bytes() if target.exists() else None,
            ))
        return cls(root=str(resolved_root), snapshots=snapshots)

    @property
    def changed_files(self) -> list[str]:
        return [snapshot.path for snapshot in self.snapshots]

    @property
    def patch_digest(self) -> str:
        hasher = hashlib.sha256()
        for snapshot in sorted(self.snapshots, key=lambda item: item.path):
            hasher.update(snapshot.path.encode("utf-8"))
            hasher.update(b"\0")
            hasher.update(b"1" if snapshot.existed else b"0")
            hasher.update(b"\0")
            hasher.update(snapshot.content or b"")
            hasher.update(b"\0")
        return hasher.hexdigest()

    def rollback(self) -> RollbackResult:
        root = Path(self.root).resolve()
        restored = []
        try:
            for snapshot in self.snapshots:
                target = (root / snapshot.path).resolve()
                if not _is_relative_to(target, root):
                    return RollbackResult(
                        rolled_back=False,
                        restored_files=restored,
                        blocker=f"path escapes repository root: {snapshot.path}",
                    )
                if snapshot.existed:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(snapshot.content or b"")
                elif target.exists():
                    target.unlink()
                restored.append(snapshot.path)
        except OSError as exc:
            return RollbackResult(rolled_back=False, restored_files=restored, blocker=str(exc))
        return RollbackResult(rolled_back=True, restored_files=restored)

    def to_evidence(self, rollback: RollbackResult | None = None) -> PatchJournalEvidence:
        return PatchJournalEvidence(
            changed_files=self.changed_files,
            patch_digest=self.patch_digest,
            rolled_back=rollback.rolled_back if rollback else False,
            blocker=rollback.blocker if rollback else None,
        )


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False
