from vad.repo.patch_apply import apply_unified_diff


def test_unified_diff_applies_to_fixture_file(tmp_path):
    target = tmp_path / "src" / "app.py"
    target.parent.mkdir()
    target.write_text("def greet():\n    return 'hi'\n", encoding="utf-8")
    patch = """--- a/src/app.py
+++ b/src/app.py
@@ -1,2 +1,2 @@
 def greet():
-    return 'hi'
+    return 'hello'
"""

    result = apply_unified_diff(tmp_path, patch)

    assert result.applied is True
    assert result.changed_files == ["src/app.py"]
    assert target.read_text(encoding="utf-8") == "def greet():\n    return 'hello'\n"
    assert result.journal is not None


def test_invalid_patch_fails_without_partial_mutation(tmp_path):
    target = tmp_path / "src" / "app.py"
    target.parent.mkdir()
    target.write_text("actual\n", encoding="utf-8")
    patch = """--- a/src/app.py
+++ b/src/app.py
@@ -1,1 +1,1 @@
-expected
+changed
"""

    result = apply_unified_diff(tmp_path, patch)

    assert result.applied is False
    assert result.blocker == "patch does not apply cleanly: src/app.py"
    assert target.read_text(encoding="utf-8") == "actual\n"


def test_patch_path_escape_is_denied(tmp_path):
    patch = """--- a/../outside.py
+++ b/../outside.py
@@ -1,1 +1,1 @@
-old
+new
"""

    result = apply_unified_diff(tmp_path, patch)

    assert result.applied is False
    assert result.blocker == "path escapes workspace root"


def test_patch_without_file_hunks_fails(tmp_path):
    result = apply_unified_diff(tmp_path, "not a patch")

    assert result.applied is False
    assert result.blocker == "no file patches found"
