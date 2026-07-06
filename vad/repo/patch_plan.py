from pathlib import PurePosixPath

from pydantic import BaseModel, Field, model_validator

from vad.contracts.models import EIP
from vad.repo.proof_discovery import DiscoveredProofCommand


class PatchPlan(BaseModel):
    eip_name: str
    scope_boundaries: list[str]
    allowed_files: list[str]
    proof_commands: list[DiscoveredProofCommand] = Field(default_factory=list)
    mees_estimate: int = Field(ge=0, le=100)
    blocker: str | None = None

    @model_validator(mode="after")
    def reject_out_of_scope_files(self):
        out_of_scope = [
            path for path in self.allowed_files
            if not _within_any_scope(path, self.scope_boundaries)
        ]
        if out_of_scope:
            raise ValueError(f"files outside EIP scope: {', '.join(out_of_scope)}")
        if not self.proof_commands and self.blocker is None:
            self.blocker = "no proof commands available for patch plan"
        return self


def build_patch_plan(
    eip: EIP,
    requested_files: list[str],
    proof_commands: list[DiscoveredProofCommand],
    mees_estimate: int,
) -> PatchPlan:
    return PatchPlan(
        eip_name=eip.name,
        scope_boundaries=eip.scope_boundaries,
        allowed_files=[_normalize_repo_path(path) for path in requested_files],
        proof_commands=proof_commands,
        mees_estimate=mees_estimate,
    )


def _within_any_scope(path: str, scope_boundaries: list[str]) -> bool:
    normalized_path = _normalize_repo_path(path)
    return any(_within_scope(normalized_path, boundary) for boundary in scope_boundaries)


def _within_scope(path: str, boundary: str) -> bool:
    normalized_boundary = _normalize_repo_path(boundary)
    return path == normalized_boundary or path.startswith(f"{normalized_boundary}/")


def _normalize_repo_path(path: str) -> str:
    normalized = PurePosixPath(path.replace("\\", "/"))
    if normalized.is_absolute() or ".." in normalized.parts:
        raise ValueError(f"unsafe repository path: {path}")
    return str(normalized)
