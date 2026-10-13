import pytest
from vad.router.evals import RoutingEvalDataset, RoutingEvaluator, RoutingScenario


def test_routing_dataset_generation_from_repo(tmp_path):
    # Setup mock repository with sensitive and normal files
    src = tmp_path / "src"
    src.mkdir()
    (src / "auth_service.py").write_text("# Sensitive auth token handler")
    (src / "crypto_utils.py").write_text("# Sensitive crypto keys")
    (src / "math_utils.py").write_text("# Math functions")
    (src / "formatter.py").write_text("# Formatter helper")

    dataset = RoutingEvalDataset.generate_from_repo(str(tmp_path.as_posix()), held_out_ratio=0.4, seed=42)
    assert len(dataset.scenarios) >= 6

    # Verify held-out partition
    held_out = dataset.get_held_out()
    train = dataset.get_train()
    assert len(held_out) > 0
    assert len(train) > 0
    assert len(held_out) + len(train) == len(dataset.scenarios)

    # Save and reload
    save_file = tmp_path / "eval_dataset.json"
    dataset.save(str(save_file.as_posix()))
    loaded = RoutingEvalDataset.load(str(save_file.as_posix()))
    assert len(loaded.scenarios) == len(dataset.scenarios)


def test_routing_evaluator_held_out_metrics(tmp_path):
    scenarios = [
        RoutingScenario(task_id="t1", repo="r", purpose="formatting", risk_tier="low", estimated_tokens=1000, budget_ceiling=1.0, expected_tier="tier1", split="held_out"),
        RoutingScenario(task_id="t2", repo="r", purpose="build", risk_tier="moderate", estimated_tokens=2000, budget_ceiling=2.0, expected_tier="tier1", split="held_out"),
        RoutingScenario(task_id="t3", repo="r", purpose="verification", risk_tier="high", estimated_tokens=5000, budget_ceiling=10.0, expected_tier="tier2", split="held_out"),
    ]
    dataset = RoutingEvalDataset(scenarios)
    evaluator = RoutingEvaluator(dataset)

    # Baseline evaluation
    res = evaluator.evaluate()
    assert res.held_out_scenarios == 3
    assert res.accuracy == 1.0
    assert res.compliance_rate == 1.0
    assert res.total_estimated_cost > 0.0

    # Custom router with sub-optimal choice (routes high risk to tier1)
    def bad_router(sc):
        return "tier1"  # always tier1

    res_bad = evaluator.evaluate(route_fn=bad_router)
    assert res_bad.accuracy < 1.0
    assert res_bad.compliance_rate < 1.0  # high risk routed to tier1 is non-compliant
