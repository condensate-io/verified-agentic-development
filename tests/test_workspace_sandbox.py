from vad.repo.workspace import WorkspaceSandbox


def test_writable_workspace_resolves_inside_root(tmp_path):
    sandbox = WorkspaceSandbox.from_path(tmp_path, writable=True)

    decision = sandbox.resolve_write("src/app.py")

    assert decision.allowed is True
    assert decision.path.endswith("src/app.py")


def test_workspace_denies_path_traversal(tmp_path):
    sandbox = WorkspaceSandbox.from_path(tmp_path, writable=True)

    decision = sandbox.resolve_write("../outside.py")

    assert decision.allowed is False
    assert decision.reason == "path escapes workspace root"


def test_workspace_denies_absolute_path(tmp_path):
    sandbox = WorkspaceSandbox.from_path(tmp_path, writable=True)

    decision = sandbox.resolve_write(tmp_path.parent / "outside.py")

    assert decision.allowed is False
    assert decision.reason == "absolute paths are not allowed"


def test_workspace_denies_symlink_escape(tmp_path):
    outside = tmp_path.parent / "outside-target"
    outside.mkdir(exist_ok=True)
    link = tmp_path / "escape"
    link.symlink_to(outside, target_is_directory=True)
    sandbox = WorkspaceSandbox.from_path(tmp_path, writable=True)

    decision = sandbox.resolve_write("escape/file.txt")

    assert decision.allowed is False
    assert decision.reason == "path escapes workspace root"


def test_read_only_workspace_denies_write(tmp_path):
    sandbox = WorkspaceSandbox.from_path(tmp_path, writable=False)

    decision = sandbox.resolve_write("src/app.py")

    assert decision.allowed is False
    assert decision.reason == "workspace is read-only"


def test_read_only_workspace_allows_read_inside_root(tmp_path):
    existing = tmp_path / "README.md"
    existing.write_text("ok", encoding="utf-8")
    sandbox = WorkspaceSandbox.from_path(tmp_path, writable=False)

    decision = sandbox.resolve_read("README.md")

    assert decision.allowed is True
    assert decision.path == str(existing.resolve())
