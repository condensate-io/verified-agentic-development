from pathlib import Path

from pydantic import BaseModel, Field

from vad.repo.journal import PatchJournal
from vad.repo.workspace import WorkspaceSandbox


class PatchApplyResult(BaseModel):
    applied: bool
    changed_files: list[str] = Field(default_factory=list)
    journal: PatchJournal | None = None
    blocker: str | None = None


class _FilePatch(BaseModel):
    path: str
    hunks: list[list[str]]


def apply_unified_diff(root: str | Path, patch_text: str) -> PatchApplyResult:
    root_path = Path(root).resolve()
    parsed = _parse_unified_diff(patch_text)
    if not parsed:
        return PatchApplyResult(applied=False, blocker="no file patches found")

    sandbox = WorkspaceSandbox.from_path(root_path, writable=True)
    planned_writes: list[tuple[str, str]] = []

    for file_patch in parsed:
        decision = sandbox.resolve_write(file_patch.path)
        if not decision.allowed:
            return PatchApplyResult(applied=False, blocker=decision.reason)

        target = Path(decision.path)
        if not target.exists():
            return PatchApplyResult(applied=False, blocker=f"target file does not exist: {file_patch.path}")

        original = target.read_text(encoding="utf-8").splitlines(keepends=True)
        applied = _apply_hunks(original, file_patch.hunks)
        if applied is None:
            return PatchApplyResult(applied=False, blocker=f"patch does not apply cleanly: {file_patch.path}")
        planned_writes.append((file_patch.path, "".join(applied)))

    journal = PatchJournal.capture(root_path, [path for path, _ in planned_writes])
    for relative_path, content in planned_writes:
        target = root_path / relative_path
        target.write_text(content, encoding="utf-8")

    return PatchApplyResult(
        applied=True,
        changed_files=[path for path, _ in planned_writes],
        journal=journal,
    )


def _parse_unified_diff(patch_text: str) -> list[_FilePatch]:
    lines = patch_text.splitlines(keepends=True)
    patches: list[_FilePatch] = []
    index = 0
    while index < len(lines):
        if not lines[index].startswith("--- "):
            index += 1
            continue
        index += 1
        if index >= len(lines) or not lines[index].startswith("+++ "):
            return []
        path = _clean_diff_path(lines[index][4:].strip())
        index += 1
        hunks = []
        while index < len(lines) and not lines[index].startswith("--- "):
            if lines[index].startswith("@@"):
                hunk = [lines[index]]
                index += 1
                while index < len(lines) and not lines[index].startswith("@@") and not lines[index].startswith("--- "):
                    hunk.append(lines[index])
                    index += 1
                hunks.append(hunk)
            else:
                index += 1
        patches.append(_FilePatch(path=path, hunks=hunks))
    return patches


def _apply_hunks(original: list[str], hunks: list[list[str]]) -> list[str] | None:
    output: list[str] = []
    cursor = 0
    for hunk in hunks:
        if not hunk:
            return None
        old_start = _old_start(hunk[0])
        if old_start is None:
            return None
        target_index = max(0, old_start - 1)
        if target_index < cursor or target_index > len(original):
            return None
        output.extend(original[cursor:target_index])
        cursor = target_index
        for line in hunk[1:]:
            if line.startswith("\\"):
                continue
            marker = line[:1]
            content = line[1:]
            if marker == " ":
                if cursor >= len(original) or original[cursor] != content:
                    return None
                output.append(content)
                cursor += 1
            elif marker == "-":
                if cursor >= len(original) or original[cursor] != content:
                    return None
                cursor += 1
            elif marker == "+":
                output.append(content)
            else:
                return None
    output.extend(original[cursor:])
    return output


def _old_start(header: str) -> int | None:
    try:
        old_range = header.split(" ", 2)[1]
        start = old_range.lstrip("-").split(",", 1)[0]
        return int(start)
    except (IndexError, ValueError):
        return None


def _clean_diff_path(path: str) -> str:
    if path.startswith("b/") or path.startswith("a/"):
        return path[2:]
    return path
