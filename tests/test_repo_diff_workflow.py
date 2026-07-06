from vad.policy.decisions import PolicyDecision
from vad.repo.diff_workflow import apply_approved_diff_proposal, create_diff_proposal


def approved(reason: str) -> PolicyDecision:
    return PolicyDecision(allow=True, reasons=[reason])


def denied(reason: str) -> PolicyDecision:
    return PolicyDecision(allow=False, denials=[reason], requires_human=True)


def patch_text() -> str:
    return """--- a/src/app.py
+++ b/src/app.py
@@ -1,2 +1,2 @@
 def greet():
-    return 'hi'
+    return 'hello'
"""


def test_diff_proposal_records_digest_before_mutation(tmp_path):
    target = tmp_path / "src" / "app.py"
    target.parent.mkdir()
    target.write_text("def greet():\n    return 'hi'\n", encoding="utf-8")

    proposal = create_diff_proposal(
        run_id="run-diff",
        task_id="patch-app",
        submitted_by="builder",
        role="builder",
        patch_text=patch_text(),
        changed_files=["src/app.py"],
        summary="Update greeting.",
    )

    assert proposal.patch_digest
    assert proposal.changed_files == ["src/app.py"]
    assert target.read_text(encoding="utf-8") == "def greet():\n    return 'hi'\n"


def test_policy_checked_diff_apply_requires_verifier_and_release_guardian(tmp_path):
    target = tmp_path / "src" / "app.py"
    target.parent.mkdir()
    target.write_text("def greet():\n    return 'hi'\n", encoding="utf-8")
    proposal = create_diff_proposal(
        run_id="run-diff",
        task_id="patch-app",
        submitted_by="builder",
        role="builder",
        patch_text=patch_text(),
        changed_files=["src/app.py"],
        summary="Update greeting.",
    )

    verifier_blocked = apply_approved_diff_proposal(
        tmp_path,
        proposal,
        verifier_decision=denied("proof missing"),
        release_guardian_decision=approved("release approved"),
    )
    guardian_blocked = apply_approved_diff_proposal(
        tmp_path,
        proposal,
        verifier_decision=approved("proof passed"),
        release_guardian_decision=denied("release missing"),
    )

    assert verifier_blocked.applied is False
    assert verifier_blocked.blocker == "verifier approval required before diff apply"
    assert guardian_blocked.applied is False
    assert guardian_blocked.blocker == "release guardian approval required before diff apply"
    assert target.read_text(encoding="utf-8") == "def greet():\n    return 'hi'\n"


def test_policy_checked_diff_apply_records_before_and_after_evidence(tmp_path):
    target = tmp_path / "src" / "app.py"
    target.parent.mkdir()
    target.write_text("def greet():\n    return 'hi'\n", encoding="utf-8")
    proposal = create_diff_proposal(
        run_id="run-diff",
        task_id="patch-app",
        submitted_by="builder",
        role="builder",
        patch_text=patch_text(),
        changed_files=["src/app.py"],
        summary="Update greeting.",
    )

    record = apply_approved_diff_proposal(
        tmp_path,
        proposal,
        verifier_decision=approved("proof passed"),
        release_guardian_decision=approved("release approved"),
    )

    assert record.applied is True
    assert record.changed_files == ["src/app.py"]
    assert record.before_digest
    assert record.after_file_digests["src/app.py"]
    assert record.verifier_decision.reasons == ["proof passed"]
    assert record.release_guardian_decision.reasons == ["release approved"]
    assert target.read_text(encoding="utf-8") == "def greet():\n    return 'hello'\n"


def test_policy_checked_diff_apply_uses_workspace_sandbox(tmp_path):
    proposal = create_diff_proposal(
        run_id="run-diff",
        task_id="patch-app",
        submitted_by="builder",
        role="builder",
        patch_text="""--- a/../outside.py
+++ b/../outside.py
@@ -1,1 +1,1 @@
-old
+new
""",
        changed_files=["outside.py"],
        summary="Unsafe path.",
    )

    record = apply_approved_diff_proposal(
        tmp_path,
        proposal,
        verifier_decision=approved("proof passed"),
        release_guardian_decision=approved("release approved"),
    )

    assert record.applied is False
    assert record.blocker == "path escapes workspace root"
