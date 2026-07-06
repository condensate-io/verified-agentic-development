import pytest

from vad.repo.git import inspect_git_state, require_clean_for_patch
from vad.repo.intake import DirtyState, RepositoryIntakeError


class FakeCompletedProcess:
    def __init__(self, stdout="", returncode=0):
        self.stdout = stdout
        self.returncode = returncode


def make_git_repo(tmp_path):
    repo = tmp_path / "repo"
    git_dir = repo / ".git"
    (git_dir / "refs" / "heads").mkdir(parents=True)
    (git_dir / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (git_dir / "refs" / "heads" / "main").write_text("abc123\n", encoding="utf-8")
    (repo / "app.py").write_text("print('ok')\n", encoding="utf-8")
    return repo


def fake_runner(status_stdout=""):
    def runner(command, **kwargs):
        if command[:3] == ["git", "rev-parse", "HEAD"]:
            return FakeCompletedProcess("abc123\n")
        if command[:3] == ["git", "status", "--porcelain"]:
            return FakeCompletedProcess(status_stdout)
        return FakeCompletedProcess(returncode=1)
    return runner


def test_clean_fixture_repo_allows_autonomous_patch(tmp_path):
    repo = make_git_repo(tmp_path)

    state = require_clean_for_patch(repo, runner=fake_runner(""))

    assert state.autonomous_patch_allowed is True
    assert state.base_revision == "abc123"
    assert state.branch == "main"


def test_dirty_fixture_repo_blocks_autonomous_patch(tmp_path):
    repo = make_git_repo(tmp_path)

    with pytest.raises(RepositoryIntakeError, match="dirty worktree"):
        require_clean_for_patch(repo, runner=fake_runner(" M app.py\n"))


def test_dirty_fixture_repo_can_be_explicitly_allowed(tmp_path):
    repo = make_git_repo(tmp_path)

    state = inspect_git_state(repo, runner=fake_runner(" M app.py\n"), allow_dirty=True)

    assert state.dirty_state == DirtyState.DIRTY
    assert state.autonomous_patch_allowed is True


def test_unknown_dirty_state_blocks_autonomous_patch(tmp_path):
    repo = make_git_repo(tmp_path)

    def runner(command, **kwargs):
        raise FileNotFoundError("git")

    state = inspect_git_state(repo, runner=runner)

    assert state.dirty_state == DirtyState.UNKNOWN
    assert state.autonomous_patch_allowed is False
    assert state.blocker == "unknown dirty state blocks autonomous patch"
