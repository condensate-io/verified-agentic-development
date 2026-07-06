from pydantic import BaseModel, Field

from vad.policy.decisions import PolicyDecision


class DependencyChange(BaseModel):
    path: str
    ecosystem: str


class DependencyChangeAssessment(BaseModel):
    changes: list[DependencyChange] = Field(default_factory=list)
    approved: bool = False

    @property
    def has_dependency_changes(self) -> bool:
        return bool(self.changes)

    def decision(self) -> PolicyDecision:
        if self.has_dependency_changes and not self.approved:
            return PolicyDecision(
                allow=False,
                denials=["dependency changes require explicit approval"],
                requires_human=True,
            )
        return PolicyDecision(allow=True, reasons=["dependency changes approved" if self.changes else "no dependency changes"])


def assess_dependency_changes(changed_files: list[str], approved: bool = False) -> DependencyChangeAssessment:
    return DependencyChangeAssessment(
        changes=[
            DependencyChange(path=path, ecosystem=ecosystem)
            for path in changed_files
            if (ecosystem := dependency_ecosystem(path)) is not None
        ],
        approved=approved,
    )


def dependency_ecosystem(path: str) -> str | None:
    name = path.replace("\\", "/").rsplit("/", 1)[-1]
    return _DEPENDENCY_MANIFESTS.get(name)


_DEPENDENCY_MANIFESTS = {
    "pyproject.toml": "python",
    "requirements.txt": "python",
    "poetry.lock": "python",
    "package.json": "node",
    "package-lock.json": "node",
    "pnpm-lock.yaml": "node",
    "yarn.lock": "node",
    "go.mod": "go",
    "go.sum": "go",
    "Cargo.toml": "rust",
    "Cargo.lock": "rust",
}
