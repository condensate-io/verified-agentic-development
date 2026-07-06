import pytest

from vad.repo.intake import DirtyState, RepositoryIntakeError, VcsType, assess_repository


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
    (repo / "pyproject.toml").write_text("[project]\nname = 'fixture'\n", encoding="utf-8")
    (repo / "app.py").write_text("print('ok')\n", encoding="utf-8")
    return repo


def test_assess_repository_records_git_state_manifests_and_languages(tmp_path):
    repo = make_git_repo(tmp_path)

    def runner(command, **kwargs):
        if command[:3] == ["git", "rev-parse", "HEAD"]:
            return FakeCompletedProcess("abc123\n")
        if command[:3] == ["git", "status", "--porcelain"]:
            return FakeCompletedProcess("")
        return FakeCompletedProcess(returncode=1)

    intake = assess_repository(repo, runner=runner)

    assert intake.vcs_type == VcsType.GIT
    assert intake.default_branch == "main"
    assert intake.current_branch == "main"
    assert intake.base_revision == "abc123"
    assert intake.dirty_state == DirtyState.CLEAN
    assert intake.manifest_files[0].path == "pyproject.toml"
    assert "python" in intake.language_hints


def test_assess_repository_records_dirty_state(tmp_path):
    repo = make_git_repo(tmp_path)

    def runner(command, **kwargs):
        if command[:3] == ["git", "status", "--porcelain"]:
            return FakeCompletedProcess(" M app.py\n")
        return FakeCompletedProcess("abc123\n")

    intake = assess_repository(repo, runner=runner)

    assert intake.dirty_state == DirtyState.DIRTY


def test_non_repository_path_fails_with_controlled_error(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()

    with pytest.raises(RepositoryIntakeError, match="No supported VCS"):
        assess_repository(plain)


def test_unsupported_vcs_fails_closed(tmp_path):
    repo = tmp_path / "repo"
    (repo / ".hg").mkdir(parents=True)

    with pytest.raises(RepositoryIntakeError, match="Unsupported VCS"):
        assess_repository(repo)


def test_git_unavailable_preserves_unknown_dirty_state(tmp_path):
    repo = make_git_repo(tmp_path)

    def runner(command, **kwargs):
        raise FileNotFoundError("git")

    intake = assess_repository(repo, runner=runner)

    assert intake.dirty_state == DirtyState.UNKNOWN
    assert intake.warnings == ["git status unavailable; dirty state unknown"]
