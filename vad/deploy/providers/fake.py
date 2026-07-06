from dataclasses import dataclass, field

from vad.deploy.models import DeploymentPlan


@dataclass
class FakeDeploymentProvider:
    targets: dict[str, str] = field(default_factory=dict)
    deployments: dict[str, dict] = field(default_factory=dict)
    _counter: int = 0

    def dry_run(self, plan: DeploymentPlan) -> dict:
        return {
            "event": "deployment_dry_run",
            "provider": "fake",
            "plan_id": plan.plan_id,
            "target_id": plan.target.target_id,
            "environment": plan.target.environment.value,
            "strategy": plan.target.strategy.value,
            "artifact_digest": plan.target.artifact_digest,
            "would_replace_artifact": self.targets.get(plan.target.target_id),
            "side_effects": False,
        }

    def apply(self, plan: DeploymentPlan) -> dict:
        self._counter += 1
        deployment_id = f"fake-deployment-{self._counter}"
        target_id = plan.target.target_id
        prior_artifact = self.targets.get(target_id)
        self.targets[target_id] = plan.target.artifact_digest

        record = {
            "event": "deployment_apply",
            "provider": "fake",
            "deployment_id": deployment_id,
            "plan_id": plan.plan_id,
            "target_id": target_id,
            "environment": plan.target.environment.value,
            "strategy": plan.target.strategy.value,
            "prior_artifact_digest": prior_artifact,
            "artifact_digest": plan.target.artifact_digest,
            "status": "applied",
            "rollback_enabled": plan.target.rollback.enabled,
        }
        self.deployments[deployment_id] = record
        return dict(record)

    def rollback(self, deployment_id: str) -> dict:
        record = self.deployments.get(deployment_id)
        if record is None:
            return {
                "event": "deployment_rollback",
                "provider": "fake",
                "deployment_id": deployment_id,
                "status": "blocked",
                "blocker": "deployment not found",
            }
        if not record["rollback_enabled"]:
            return {
                "event": "deployment_rollback",
                "provider": "fake",
                "deployment_id": deployment_id,
                "target_id": record["target_id"],
                "status": "blocked",
                "blocker": "rollback disabled",
            }

        target_id = record["target_id"]
        prior_artifact = record["prior_artifact_digest"]
        if prior_artifact is None:
            self.targets.pop(target_id, None)
        else:
            self.targets[target_id] = prior_artifact
        record["status"] = "rolled_back"

        return {
            "event": "deployment_rollback",
            "provider": "fake",
            "deployment_id": deployment_id,
            "target_id": target_id,
            "status": "rolled_back",
            "restored_artifact_digest": prior_artifact,
        }
