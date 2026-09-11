import pytest
from pathlib import Path
from vad.onboarding.repo_onboarder import RepoOnboarder, main
from vad.decision.hybrid_router import HybridRouter


def test_repo_onboarder_flow(tmp_path):
    repo_dir = tmp_path / "repos" / "billing-service"
    repo_dir.mkdir(parents=True)
    (repo_dir / "README.md").write_text("# Billing Service\nProcesses customer invoices and payments with Stripe integration.")
    (repo_dir / "pyproject.toml").write_text("[project]\nname = 'billing'")

    corpus_dir = tmp_path / "corpus"
    agents_dir = tmp_path / "agents"

    router = HybridRouter()
    onboarder = RepoOnboarder(
        corpus_dir=str(corpus_dir),
        agents_dir=str(agents_dir),
        hybrid_router=router,
    )

    res = onboarder.onboard_repository(repo_dir, git_url="git@github.com:corp/billing-service.git")
    assert res["status"] == "onboarded"
    assert res["repo_name"] == "billing-service"
    assert "billing" in res["routing_signals"]
    assert (corpus_dir / "billing-service.yaml").exists()
    assert (agents_dir / "billing-service_agent.yaml").exists()

    # Verify that hybrid router now routes queries matching billing
    decision = router.route("Generate billing invoice")
    assert decision.is_routed is True
    assert decision.target_agent == "billing-service-agent"


def test_repo_onboarder_cli(tmp_path):
    repo_dir = tmp_path / "test-repo"
    repo_dir.mkdir()
    (repo_dir / "README.md").write_text("# Test Repo\nSimple test component")

    corpus_dir = tmp_path / "corpus"
    ret = main(["--dir", str(repo_dir), "--name", "cli-test", "--corpus", str(corpus_dir)])
    assert ret == 0
    assert (corpus_dir / "cli-test.yaml").exists()
