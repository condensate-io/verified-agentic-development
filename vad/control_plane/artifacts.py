from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from vad.control_plane.claude_code_package import build_claude_code_package
from vad.control_plane.codex_package import build_codex_package
from vad.control_plane.cursor_package import build_cursor_package
from vad.control_plane.generic_mcp_package import build_generic_mcp_package
from vad.control_plane.opencode_package import build_opencode_package
from vad.control_plane.plugins import VADPluginManifest
from vad.control_plane.vscode_package import build_vscode_package
from vad.control_plane.windsurf_package import build_windsurf_package
from vad.signing.local import payload_digest


class ReproducibleArtifact(BaseModel):
    path: str = Field(min_length=1)
    digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    bytes: int = Field(ge=1)


class ReproducibleArtifactReport(BaseModel):
    root: str
    artifact_count: int = Field(ge=1)
    artifacts: tuple[ReproducibleArtifact, ...]
    report_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    secret_scan_passed: bool


def build_reproducible_artifacts(output_root: Path, *, project_root: Path | None = None) -> ReproducibleArtifactReport:
    project_root = project_root or Path(__file__).resolve().parents[2]
    output_root = output_root.resolve()
    files = _artifact_payloads(project_root)
    artifacts: list[ReproducibleArtifact] = []

    for relative_path, payload in sorted(files.items()):
        text = _stable_json(payload)
        _reject_secret_text(relative_path, text)
        target = output_root / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text + "\n", encoding="utf-8")
        artifacts.append(ReproducibleArtifact(
            path=relative_path,
            digest=payload_digest(text),
            bytes=len((text + "\n").encode("utf-8")),
        ))

    report_payload = {
        "root": str(output_root),
        "artifacts": [artifact.model_dump(mode="json") for artifact in artifacts],
        "secret_scan_passed": True,
    }
    report = ReproducibleArtifactReport(
        root=str(output_root),
        artifact_count=len(artifacts),
        artifacts=tuple(artifacts),
        report_digest=payload_digest(report_payload),
        secret_scan_passed=True,
    )
    report_path = output_root / "digest-report.json"
    report_text = _stable_json(report.model_dump(mode="json"))
    report_path.write_text(report_text + "\n", encoding="utf-8")
    return report


def _artifact_payloads(project_root: Path) -> dict[str, Any]:
    pyproject = (project_root / "pyproject.toml").read_text(encoding="utf-8")
    core_version = _read_project_version(pyproject)
    payloads: dict[str, Any] = {
        f"core/verified-agentic-development-{core_version}/package.json": {
            "name": "verified-agentic-development",
            "version": core_version,
            "commands": ["vad"],
            "local_only": True,
        }
    }

    for schema_name in ["vad-plugin-manifest.schema.json", "eip.schema.json"]:
        payloads[f"schemas/{schema_name}"] = json.loads((project_root / "schemas" / schema_name).read_text(encoding="utf-8"))

    for manifest in _plugin_manifests():
        payloads[f"plugins/{manifest.plugin_id}/{manifest.version}/manifest.json"] = manifest.model_dump(mode="json")

    return payloads


def _plugin_manifests() -> tuple[VADPluginManifest, ...]:
    packages = (
        build_generic_mcp_package(),
        build_claude_code_package(),
        build_codex_package(),
        build_vscode_package(),
        build_cursor_package(),
        build_windsurf_package(),
        build_opencode_package(),
    )
    return tuple(package.manifest for package in packages)


def _read_project_version(pyproject: str) -> str:
    match = re.search(r'^version = "([^"]+)"$', pyproject, re.MULTILINE)
    if match is None:
        raise ValueError("pyproject.toml does not define [project].version")
    return match.group(1)


def _stable_json(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _reject_secret_text(path: str, text: str) -> None:
    patterns = [
        r"(?i)(api[_-]?key|token|password|secret|private[_-]?key)\s*[:=]\s*[^,\s;]+",
        r"(?i)sk-[a-z0-9_-]{6,}",
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
    ]
    if any(re.search(pattern, text) for pattern in patterns):
        raise ValueError(f"artifact {path} contains a secret marker")
