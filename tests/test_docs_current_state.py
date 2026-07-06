from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
DOCS_ROOT = REPO_ROOT / "docs"


def test_docs_use_current_state_language_instead_of_rollout_markers():
    forbidden_terms = [
        " wave",
        "wave ",
        "wave.",
        "wave;",
        "wave:",
        "this wave",
        "local wave",
        "implementation wave",
        "Phase 1",
        "Phase 2",
        "Phase 3",
        "Phase 4",
        "Phase 5",
        "Phase 6",
        "Phase 7",
        "Phase 8",
        "Phase 9",
        "Phase 10",
        "0207",
        "2806",
    ]

    findings = []
    for path in sorted(DOCS_ROOT.rglob("*.md")):
        text = path.read_text(encoding="utf-8")
        lowered = text.lower()
        for term in forbidden_terms:
            if term.lower() in lowered:
                findings.append(f"{path.relative_to(REPO_ROOT)} contains {term!r}")

    assert findings == []


def test_evolution_plan_covers_central_orchestrator_os_gaps():
    text = (DOCS_ROOT / "evolution-plan.md").read_text(encoding="utf-8")

    for phrase in [
        "automated agentic central orchestrator OS",
        "Durable Work Queue And Scheduler",
        "Lease Recovery And Reassignment",
        "Run And Task State Machine",
        "Live Local Client Connectors",
        "Persistent Plugin Inventory",
        "Policy, Budget, And MEES Governance",
        "Terminal And Proof Streaming",
        "Diff Proposal And Apply Workflow",
        "Operator Command Surface",
        "Secrets And Signing Hardening",
        "Reference Architecture Documentation",
        "Track A, Orchestrator Core",
        "add a SQLite-backed `work_items` table and service",
    ]:
        assert phrase in text


def test_evolution_plan_keeps_current_cloud_boundary_explicit():
    text = (DOCS_ROOT / "evolution-plan.md").read_text(encoding="utf-8")
    normalized = " ".join(text.split())

    for phrase in [
        "not hosted SaaS",
        "managed tenancy",
        "cloud dashboard",
        "remote MCP hosting",
        "production key management",
        "automatic package publication",
        "automatic plugin installation",
        "live production deployment",
    ]:
        assert phrase in normalized
