from typing import Any

from vad.contracts.models import EIP
from vad.contracts.normalize import normalize_eip


def diff_eips(old: EIP, new: EIP) -> list[dict[str, Any]]:
    return _diff_values("", normalize_eip(old), normalize_eip(new))


def format_diff(changes: list[dict[str, Any]]) -> str:
    if not changes:
        return "No EIP changes.\n"
    lines = []
    for change in changes:
        path = change["path"] or "."
        kind = change["kind"]
        if kind == "added":
            lines.append(f"added {path}: {change['new']}")
        elif kind == "removed":
            lines.append(f"removed {path}: {change['old']}")
        else:
            lines.append(f"changed {path}: {change['old']} -> {change['new']}")
    return "\n".join(lines) + "\n"


def _join_path(prefix: str, key: str) -> str:
    return f"{prefix}.{key}" if prefix else key


def _diff_values(path: str, old: Any, new: Any) -> list[dict[str, Any]]:
    if isinstance(old, dict) and isinstance(new, dict):
        changes = []
        for key in sorted(set(old) | set(new)):
            next_path = _join_path(path, key)
            if key not in old:
                changes.append({"kind": "added", "path": next_path, "old": None, "new": new[key]})
            elif key not in new:
                changes.append({"kind": "removed", "path": next_path, "old": old[key], "new": None})
            else:
                changes.extend(_diff_values(next_path, old[key], new[key]))
        return changes

    if isinstance(old, list) and isinstance(new, list):
        changes = []
        max_len = max(len(old), len(new))
        for index in range(max_len):
            next_path = f"{path}[{index}]"
            if index >= len(old):
                changes.append({"kind": "added", "path": next_path, "old": None, "new": new[index]})
            elif index >= len(new):
                changes.append({"kind": "removed", "path": next_path, "old": old[index], "new": None})
            else:
                changes.extend(_diff_values(next_path, old[index], new[index]))
        return changes

    if old != new:
        return [{"kind": "changed", "path": path, "old": old, "new": new}]
    return []
