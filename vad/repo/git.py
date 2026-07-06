from pathlib import Path

from pydantic import BaseModel

from vad.repo.intake import DirtyState, RepositoryIntakeError, assess_repository


class GitState(BaseModel):
    root: str
    branch: str | None = None
    base_revision: str | None = None
    dirty_state: DirtyState
    autonomous_patch_allowed: bool
    blocker: str | None = None


def inspect_git_state(path: str | Path, runner=None, allow_dirty: bool = False) -> GitState:
    intake = assess_repository(path, runner=runner)
    blocker = None
    allowed = True

    if intake.dirty_state == DirtyState.DIRTY and not allow_dirty:
        allowed = False
        blocker = "dirty worktree blocks autonomous patch"
    elif intake.dirty_state == DirtyState.UNKNOWN:
        allowed = False
        blocker = "unknown dirty state blocks autonomous patch"

    return GitState(
        root=intake.root,
        branch=intake.current_branch,
        base_revision=intake.base_revision,
        dirty_state=intake.dirty_state,
        autonomous_patch_allowed=allowed,
        blocker=blocker,
    )


def require_clean_for_patch(path: str | Path, runner=None) -> GitState:
    state = inspect_git_state(path, runner=runner, allow_dirty=False)
    if not state.autonomous_patch_allowed:
        raise RepositoryIntakeError(state.blocker or "repository is not safe for autonomous patch")
    return state
