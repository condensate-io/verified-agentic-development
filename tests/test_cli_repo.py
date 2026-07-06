import json
import subprocess
import sys
from unittest.mock import patch

import pytest

from vad.cli import main
from vad.contracts.models import (
    AutonomyTier,
    Constraints,
    EIP,
    Goal,
    Invariants,
    ModelBudget,
    ProofObligation,
    ReleaseRequirements,
    RiskTier,
    TelemetryRequirements,
    ToolPermissions,
)
from vad.proof.plan import ProofMapping, ProofPlan, compute_eip_digest
from vad.repo.git import GitState
from vad.repo.intake import DirtyState

REAL_SUBPROCESS_RUN = subprocess.run


class FakeCompletedProcess:
    def __init__(self, stdout="", returncode=0, stderr=""):
        self.stdout = stdout
        self.returncode = returncode
        self.stderr = stderr


def make_git_repo(tmp_path):
    repo = tmp_path / "repo"
    git_dir = repo / ".git"
    (git_dir / "refs" / "heads").mkdir(parents=True)
    (git_dir / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (git_dir / "refs" / "heads" / "main").write_text("abc123\n", encoding="utf-8")
    (repo / "pyproject.toml").write_text("[project]\nname = 'fixture'\n", encoding="utf-8")
    return repo


def test_cli_repo_assess_emits_json(tmp_path, capsys):
    repo = make_git_repo(tmp_path)

    def runner(command, **kwargs):
        if command[:3] == ["git", "rev-parse", "HEAD"]:
            return FakeCompletedProcess("abc123\n")
        if command[:3] == ["git", "status", "--porcelain"]:
            return FakeCompletedProcess("")
        return FakeCompletedProcess(returncode=1)

    with patch("vad.repo.intake.subprocess.run", runner):
        with patch.object(sys, "argv", ["vad", "repo", "assess", str(repo)]):
            main()

    payload = json.loads(capsys.readouterr().out)
    assert payload["vcs_type"] == "git"
    assert payload["default_branch"] == "main"
    assert payload["dirty_state"] == "clean"
    assert payload["manifest_files"][0]["path"] == "pyproject.toml"


def test_cli_repo_assess_missing_repo_exits_nonzero(tmp_path):
    with patch.object(sys, "argv", ["vad", "repo", "assess", str(tmp_path / "missing")]):
        with pytest.raises(SystemExit) as e:
            main()

    assert e.value.code == 1


def test_cli_repo_assess_dirty_state_is_machine_readable(tmp_path, capsys):
    repo = make_git_repo(tmp_path)

    def runner(command, **kwargs):
        if command[:3] == ["git", "status", "--porcelain"]:
            return FakeCompletedProcess(" M app.py\n")
        return FakeCompletedProcess("abc123\n")

    with patch("vad.repo.intake.subprocess.run", runner):
        with patch.object(sys, "argv", ["vad", "repo", "assess", str(repo)]):
            main()

    payload = json.loads(capsys.readouterr().out)
    assert payload["dirty_state"] == "dirty"


def make_patch_fixture(tmp_path):
    repo = tmp_path / "repo"
    git_dir = repo / ".git"
    (git_dir / "refs" / "heads").mkdir(parents=True)
    (git_dir / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (git_dir / "refs" / "heads" / "main").write_text("abc123\n", encoding="utf-8")
    (repo / "src").mkdir(parents=True)
    (repo / "tests").mkdir()
    (repo / "src" / "__init__.py").write_text("", encoding="utf-8")
    (repo / "src" / "app.py").write_text("def greet():\n    return 'hi'\n", encoding="utf-8")
    (repo / "tests" / "test_app.py").write_text(
        "import sys\n"
        "from pathlib import Path\n\n"
        "sys.path.insert(0, str(Path(__file__).resolve().parents[1]))\n\n"
        "from src.app import greet\n\n"
        "def test_greet():\n"
        "    assert greet() == 'hello'\n",
        encoding="utf-8",
    )
    return repo


def make_repo_patch_eip():
    return EIP(
        version="1.0.0",
        name="repo-patch",
        goal=Goal(description="Patch repo", success_criteria=["tests pass"]),
        non_goals=[],
        risk_tier=RiskTier.LOW,
        autonomy_tier=AutonomyTier.BOUNDED,
        scope_boundaries=["src", "tests"],
        invariants=Invariants(),
        constraints=Constraints(),
        proof_obligations=[ProofObligation(id="po-1", kind="unit", description="unit tests")],
        tool_permissions=ToolPermissions(allowed=["pytest"], denied=["network"]),
        memory_requirements=[],
        model_budget=ModelBudget(max_tokens=1000, max_cost=1.0, max_loop_depth=3),
        release_requirements=ReleaseRequirements(required=False, gates=[]),
        telemetry_requirements=TelemetryRequirements(required=False, signals=[]),
    )


def write_eip_and_plan(tmp_path, eip):
    eip_file = tmp_path / "eip.json"
    plan_file = tmp_path / "proof.json"
    eip_file.write_text(json.dumps(eip.model_dump(mode="json")), encoding="utf-8")
    plan = ProofPlan(
        eip_version=eip.version,
        eip_digest=compute_eip_digest(eip),
        mappings=[ProofMapping(obligation_id="po-1", test_command="pytest tests/test_app.py")],
    )
    plan_file.write_text(json.dumps(plan.model_dump(mode="json")), encoding="utf-8")
    return eip_file, plan_file


def clean_git_state(repo):
    return GitState(
        root=str(repo),
        branch="main",
        base_revision="abc123",
        dirty_state=DirtyState.CLEAN,
        autonomous_patch_allowed=True,
    )


def test_cli_repo_patch_applies_patch_and_records_evidence(tmp_path, capsys):
    repo = make_patch_fixture(tmp_path)
    eip_file, plan_file = write_eip_and_plan(tmp_path, make_repo_patch_eip())
    patch_file = tmp_path / "change.patch"
    patch_file.write_text("""--- a/src/app.py
+++ b/src/app.py
@@ -1,2 +1,2 @@
 def greet():
-    return 'hi'
+    return 'hello'
""", encoding="utf-8")

    with patch("vad.repo.git.inspect_git_state", return_value=clean_git_state(repo)):
        with patch.object(sys, "argv", [
            "vad", "repo", "patch", str(repo), str(eip_file), str(plan_file), "--patch", str(patch_file),
        ]):
            main()

    payload = json.loads(capsys.readouterr().out)
    assert payload["applied"] is True
    assert payload["changed_files"] == ["src/app.py"]
    assert payload["journal"]["patch_digest"]
    assert payload["verification"]["passed"] is True
    assert (repo / "src" / "app.py").read_text(encoding="utf-8").endswith("return 'hello'\n")


def test_cli_repo_patch_failing_proof_rolls_back_with_journal(tmp_path, capsys):
    repo = make_patch_fixture(tmp_path)
    eip_file, plan_file = write_eip_and_plan(tmp_path, make_repo_patch_eip())
    patch_file = tmp_path / "change.patch"
    patch_file.write_text("""--- a/src/app.py
+++ b/src/app.py
@@ -1,2 +1,2 @@
 def greet():
-    return 'hi'
+    return 'wrong'
""", encoding="utf-8")

    with patch("vad.repo.git.inspect_git_state", return_value=clean_git_state(repo)):
        with patch.object(sys, "argv", [
            "vad", "repo", "patch", str(repo), str(eip_file), str(plan_file), "--patch", str(patch_file),
        ]):
            with pytest.raises(SystemExit) as e:
                main()

    assert e.value.code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["verification"]["passed"] is False
    assert payload["rolled_back"] is True
    assert payload["journal"]["rolled_back"] is True
    assert (repo / "src" / "app.py").read_text(encoding="utf-8").endswith("return 'hi'\n")


def clean_intake_runner(command, **kwargs):
    if command[:3] == ["git", "rev-parse", "HEAD"]:
        return FakeCompletedProcess("abc123\n")
    if command[:3] == ["git", "status", "--porcelain"]:
        return FakeCompletedProcess("")
    return REAL_SUBPROCESS_RUN(command, **kwargs)


def test_cli_repo_run_modifies_and_verifies_fixture_repo(tmp_path, capsys):
    repo = make_patch_fixture(tmp_path)
    ask_file = tmp_path / "ask.txt"
    ask_file.write_text("In src, fix the greeting bug; success must be proven by tests.", encoding="utf-8")
    patch_file = tmp_path / "change.patch"
    patch_file.write_text("""--- a/src/app.py
+++ b/src/app.py
@@ -1,2 +1,2 @@
 def greet():
-    return 'hi'
+    return 'hello'
""".replace("++++", "+++"), encoding="utf-8")

    with patch("vad.repo.intake.subprocess.run", clean_intake_runner):
        with patch("vad.repo.git.inspect_git_state", return_value=clean_git_state(repo)):
            with patch.object(sys, "argv", [
                "vad", "repo", "run", str(repo), str(ask_file), "--patch", str(patch_file),
            ]):
                main()

    payload = json.loads(capsys.readouterr().out)
    assert payload["decision"] == "passed"
    assert payload["patch"]["verification"]["passed"] is True
    assert payload["patch"]["journal"]["patch_digest"]
    assert (repo / "src" / "app.py").read_text(encoding="utf-8").endswith("return 'hello'\n")


def test_cli_repo_run_dependency_change_blocks_without_approval(tmp_path, capsys):
    repo = make_patch_fixture(tmp_path)
    (repo / "requirements.txt").write_text("pydantic\n", encoding="utf-8")
    ask_file = tmp_path / "ask.txt"
    ask_file.write_text("In requirements, add a package pin; success must be proven by tests.", encoding="utf-8")
    patch_file = tmp_path / "deps.patch"
    patch_file.write_text("""--- a/requirements.txt
+++ b/requirements.txt
@@ -1 +1,2 @@
 pydantic
+requests
""".replace("++++", "+++"), encoding="utf-8")

    with patch("vad.repo.intake.subprocess.run", clean_intake_runner):
        with patch("vad.repo.git.inspect_git_state", return_value=clean_git_state(repo)):
            with patch.object(sys, "argv", [
                "vad", "repo", "run", str(repo), str(ask_file), "--patch", str(patch_file),
            ]):
                with pytest.raises(SystemExit) as e:
                    main()

    assert e.value.code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["decision"] == "blocked"
    assert payload["patch"]["dependency_decision"]["allow"] is False
    assert payload["patch"]["rolled_back"] is True
    assert (repo / "requirements.txt").read_text(encoding="utf-8") == "pydantic\n"


def test_cli_repo_run_failing_patch_rolls_back(tmp_path, capsys):
    repo = make_patch_fixture(tmp_path)
    ask_file = tmp_path / "ask.txt"
    ask_file.write_text("In src, fix the greeting bug; success must be proven by tests.", encoding="utf-8")
    patch_file = tmp_path / "bad.patch"
    patch_file.write_text("""--- a/src/app.py
+++ b/src/app.py
@@ -1,2 +1,2 @@
 def greet():
-    return 'hi'
+    return 'wrong'
""".replace("++++", "+++"), encoding="utf-8")

    with patch("vad.repo.intake.subprocess.run", clean_intake_runner):
        with patch("vad.repo.git.inspect_git_state", return_value=clean_git_state(repo)):
            with patch.object(sys, "argv", [
                "vad", "repo", "run", str(repo), str(ask_file), "--patch", str(patch_file),
            ]):
                with pytest.raises(SystemExit) as e:
                    main()

    assert e.value.code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["patch"]["verification"]["passed"] is False
    assert payload["patch"]["rolled_back"] is True
    assert (repo / "src" / "app.py").read_text(encoding="utf-8").endswith("return 'hi'\n")
