from vad.deploy.models import (
    DeploymentEnvironment,
    DeploymentPlan,
    DeploymentStrategy,
    DeploymentTarget,
    RollbackPolicy,
    TelemetryRequirement,
)
from vad.deploy.providers.fake import FakeDeploymentProvider


DIGEST_A = "a" * 64
DIGEST_B = "b" * 64


def target(digest=DIGEST_A):
    return DeploymentTarget(
        target_id="staging-api",
        environment=DeploymentEnvironment.STAGING,
        provider="fake",
        strategy=DeploymentStrategy.BLUE_GREEN,
        artifact_digest=digest,
        telemetry=[
            TelemetryRequirement(
                name="healthy_requests",
                query="rate(http_requests_total[5m])",
                minimum_health=0.99,
                window_seconds=300,
            )
        ],
        rollback=RollbackPolicy(
            strategy=DeploymentStrategy.BLUE_GREEN,
            trigger_metric="healthy_requests",
            threshold=0.95,
            max_wait_seconds=600,
        ),
    )


def plan(digest=DIGEST_A):
    return DeploymentPlan(plan_id=f"plan-{digest[0]}", target=target(digest))


def test_fake_deployment_dry_run_produces_plan_evidence_without_side_effects():
    provider = FakeDeploymentProvider()

    evidence = provider.dry_run(plan())

    assert evidence["event"] == "deployment_dry_run"
    assert evidence["side_effects"] is False
    assert provider.targets == {}


def test_fake_deployment_apply_records_rollout_state():
    provider = FakeDeploymentProvider()

    record = provider.apply(plan())

    assert record["deployment_id"] == "fake-deployment-1"
    assert record["status"] == "applied"
    assert record["artifact_digest"] == DIGEST_A
    assert provider.targets["staging-api"] == DIGEST_A


def test_fake_deployment_rollback_restores_prior_target_state():
    provider = FakeDeploymentProvider(targets={"staging-api": DIGEST_A})
    record = provider.apply(plan(DIGEST_B))

    rollback = provider.rollback(record["deployment_id"])

    assert rollback["status"] == "rolled_back"
    assert rollback["restored_artifact_digest"] == DIGEST_A
    assert provider.targets["staging-api"] == DIGEST_A


def test_fake_deployment_rollback_blocks_unknown_deployment():
    provider = FakeDeploymentProvider()

    rollback = provider.rollback("missing")

    assert rollback["status"] == "blocked"
    assert rollback["blocker"] == "deployment not found"
