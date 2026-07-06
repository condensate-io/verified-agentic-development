from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_final_recovery_audit_records_gates_and_limitations():
    audit = (REPO_ROOT / "docs" / "final-recovery-audit.md").read_text(encoding="utf-8")

    assert "recoverable current state" in audit
    assert "source-of-truth tracker is at the workspace root" in audit
    assert "518 passed, 1 skipped" in audit
    assert "45 passed in 1.34s" in audit
    assert "docker build -t vad-test:local" in audit
    assert "tests/test_final_recovery_audit.py" in audit
    assert "tests/test_security_audit.py" in audit
    assert "Live model-provider calls remain opt-in" in audit
    assert "Production deployment is not implemented" in audit
    assert "UI is a local dashboard" in audit
    assert "Plugin artifacts are reviewable local artifacts only" in audit
    assert "Any future cloud or SaaS plan must be introduced by a later plan/tracker item" in audit
    assert "hosted VAD SaaS" in audit


def test_security_audit_remains_linked_to_final_recovery_scope():
    security = (REPO_ROOT / "docs" / "security-audit.md").read_text(encoding="utf-8")
    recovery = (REPO_ROOT / "docs" / "final-recovery-audit.md").read_text(encoding="utf-8")

    assert "no live credentials" in security.lower()
    assert "tests/test_security_audit.py" in security
    assert "security audit" in recovery.lower()


def test_final_recovery_audit_tracker_has_no_unchecked_tasks():
    audit = (REPO_ROOT / "docs" / "final-recovery-audit.md").read_text(encoding="utf-8")
    normalized = " ".join(audit.split())

    assert "root implementation tracker has no unchecked tracker tasks" in normalized
    assert "every task marker in that tracker is closed as `- [x]`" in normalized
    assert "Security, recovery, and documentation capabilities are complete" in normalized


def test_final_recovery_audit_keeps_future_cloud_boundary_explicit():
    control_plane = (REPO_ROOT / "docs" / "control-plane.md").read_text(encoding="utf-8")
    recovery = (REPO_ROOT / "docs" / "final-recovery-audit.md").read_text(encoding="utf-8")
    control_plane_text = " ".join(control_plane.split())
    recovery_text = " ".join(recovery.split())

    for phrase in [
        "Future Cloud Scope",
        "not implemented in the current local reference architecture",
        "hosted VAD SaaS",
        "managed tenancy",
        "cloud dashboard",
        "cloud-hosted MCP gateway",
        "production key management",
    ]:
        assert phrase in control_plane_text
        assert phrase in recovery_text
