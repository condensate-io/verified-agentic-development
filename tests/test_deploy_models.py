import pytest
from pydantic import ValidationError

from vad.deploy.models import (
    DeploymentEnvironment,
    DeploymentPlan,
    DeploymentStrategy,
    DeploymentTarget,
    RollbackPolicy,
    SecretReference,
    TelemetryRequirement,
)


DIGEST = "a" * 64


def telemetry():
    return TelemetryRequirement(
        name="healthy_requests",
        query="rate(http_requests_total[5m])",
        minimum_health=0.99,
        window_seconds=300,
    )


def rollback():
    return RollbackPolicy(
        strategy=DeploymentStrategy.BLUE_GREEN,
        trigger_metric="healthy_requests",
        threshold=0.95,
        max_wait_seconds=600,
    )


def test_deployment_target_validates_environment_strategy_telemetry_and_rollback():
    target = DeploymentTarget(
        target_id="prod-api",
        environment=DeploymentEnvironment.PRODUCTION,
        provider="fake",
        region="local",
        strategy=DeploymentStrategy.CANARY,
        artifact_digest=DIGEST,
        secret_refs=[SecretReference(name="api-token", ref="env:API_TOKEN")],
        telemetry=[telemetry()],
        rollback=rollback(),
    )

    assert target.environment == DeploymentEnvironment.PRODUCTION
    assert target.strategy == DeploymentStrategy.CANARY
    assert target.telemetry[0].minimum_health == 0.99
    assert target.rollback.enabled is True


def test_production_target_requires_telemetry_and_rollback():
    with pytest.raises(ValidationError, match="requires telemetry"):
        DeploymentTarget(
            target_id="prod-api",
            environment=DeploymentEnvironment.PRODUCTION,
            provider="fake",
            strategy=DeploymentStrategy.BLUE_GREEN,
            artifact_digest=DIGEST,
            rollback=rollback(),
        )

    with pytest.raises(ValidationError, match="requires rollback"):
        DeploymentTarget(
            target_id="prod-api",
            environment=DeploymentEnvironment.PRODUCTION,
            provider="fake",
            strategy=DeploymentStrategy.BLUE_GREEN,
            artifact_digest=DIGEST,
            telemetry=[telemetry()],
            rollback=RollbackPolicy(
                enabled=False,
                strategy=DeploymentStrategy.BLUE_GREEN,
                trigger_metric="healthy_requests",
                threshold=0.95,
                max_wait_seconds=600,
            ),
        )


def test_secret_values_are_rejected_and_secret_references_are_allowed():
    allowed = SecretReference(name="deploy-token", ref="vault:prod/deploy-token")
    assert allowed.ref == "vault:prod/deploy-token"

    with pytest.raises(ValidationError, match="inline secret values"):
        SecretReference(name="bad", ref="token=sk-1234567890abcdef")

    with pytest.raises(ValidationError, match="secret reference scheme"):
        SecretReference(name="bad", ref="plain-text-token-name")


def test_deployment_plan_records_target_and_external_references():
    plan = DeploymentPlan(
        plan_id="plan-1",
        target=DeploymentTarget(
            target_id="staging-api",
            environment=DeploymentEnvironment.STAGING,
            provider="fake",
            strategy=DeploymentStrategy.ROLLING,
            artifact_digest=DIGEST,
            rollback=rollback(),
        ),
        approval_ref="evidence:approval-1",
        evidence_ref="evidence:run-1",
    )

    assert plan.target.target_id == "staging-api"
    assert plan.approval_ref == "evidence:approval-1"
