"""Control-plane API service layer for VAD Horizon capabilities."""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from vad.contracts.intent_hierarchy import (
    EngineeringObjective,
    IntentLevel,
    Programme,
    StrategicObjective,
)
from vad.control_plane.intent_hierarchy_service import IntentHierarchyService
from vad.control_plane.remote_worker_coordinator import RemoteWorkerCoordinator
from vad.control_plane.remote_workers import (
    ArtifactTransferManifest,
    RemoteWorkerIdentity,
    RemoteWorkerPoolPolicy,
)
from vad.server.db.store import ServerStore
from vad.workflows.continuous_operations import (
    ContinuousOperationsRequest,
    ContinuousVerifiedOperationsWorkflow,
)
from vad.workflows.domain_pack_execution import (
    DomainExecutionRequest,
    DomainPackExecutionRunner,
)


class HorizonApiService:
    """Request/response handler service over ServerStore for VAD Horizon endpoints."""

    def __init__(
        self,
        store: ServerStore | None,
        *,
        remote_policy: RemoteWorkerPoolPolicy | None = None,
    ) -> None:
        self.store = store
        self.hierarchy = IntentHierarchyService(store=store) if store is not None else None
        self.domain_runner = DomainPackExecutionRunner(store=store) if store is not None else None
        self.remote_coordinator = (
            RemoteWorkerCoordinator(policy=remote_policy, store=store)
            if store is not None
            else None
        )

    @staticmethod
    def _service_unavailable() -> tuple[int, dict[str, Any]]:
        return 503, {
            "error": "service_unavailable",
            "message": "database not configured",
        }

    def create_hierarchy_node(self, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        """Register a StrategicObjective, Programme, or EngineeringObjective node."""
        if self.store is None or self.hierarchy is None:
            return self._service_unavailable()

        raw_level = body.get("level")
        level = raw_level.value if isinstance(raw_level, IntentLevel) else raw_level

        try:
            if level == IntentLevel.STRATEGIC_OBJECTIVE.value:
                node = self.hierarchy.register_strategic_objective(StrategicObjective(**body))
            elif level == IntentLevel.PROGRAMME.value:
                node = self.hierarchy.register_programme(Programme(**body))
            elif level == IntentLevel.ENGINEERING_OBJECTIVE.value:
                node = self.hierarchy.register_engineering_objective(EngineeringObjective(**body))
            else:
                raise ValueError(
                    f"Unsupported hierarchy node level {raw_level!r}; expected one of "
                    "'strategic_objective', 'programme', 'engineering_objective'"
                )
        except (ValidationError, ValueError, KeyError, TypeError) as exc:
            return 422, {"error": "invalid_hierarchy_node", "detail": str(exc)}

        return 201, {"node": node.model_dump(mode="json")}

    def list_hierarchy_nodes(
        self,
        *,
        level: str | None = None,
        parent_id: str | None = None,
    ) -> tuple[int, dict[str, Any]]:
        """List registered intent hierarchy nodes filtered by optional level and parent_id."""
        if self.store is None or self.hierarchy is None:
            return self._service_unavailable()

        parsed_level: IntentLevel | None = None
        if level is not None:
            try:
                parsed_level = IntentLevel(level)
            except ValueError as exc:
                return 422, {"error": "invalid_hierarchy_level", "detail": str(exc)}

        nodes = self.hierarchy.list_nodes(level=parsed_level)
        if parent_id is not None:
            nodes = [node for node in nodes if node.parent_id == parent_id]

        return 200, {"nodes": [node.model_dump(mode="json") for node in nodes]}

    def approve_objective(
        self,
        objective_id: str,
        body: dict[str, Any],
    ) -> tuple[int, dict[str, Any]]:
        """Approve an EngineeringObjective decomposition with role and hierarchy checks."""
        if self.store is None or self.hierarchy is None:
            return self._service_unavailable()

        approver = str(body.get("approver") or "")
        approver_role = str(body.get("approver_role") or "release_guardian")

        try:
            approved = self.hierarchy.approve_objective(
                objective_id,
                approver=approver,
                approver_role=approver_role,
            )
        except PermissionError as exc:
            return 403, {"error": "forbidden", "detail": str(exc)}
        except KeyError as exc:
            return 404, {"error": "not_found", "detail": str(exc)}
        except ValueError as exc:
            return 422, {"error": "invalid_objective_approval", "detail": str(exc)}

        return 200, {"objective": approved.model_dump(mode="json")}

    def get_objective_rollup(self, objective_id: str) -> tuple[int, dict[str, Any]]:
        """Retrieve an EngineeringObjective, its tranche schedule (if approved), and release readiness."""
        if self.store is None or self.hierarchy is None:
            return self._service_unavailable()

        try:
            node = self.hierarchy.get_node(objective_id)
            if not isinstance(node, EngineeringObjective):
                return 422, {
                    "error": "invalid_objective",
                    "detail": (
                        f"Node '{objective_id}' has level '{node.level.value}', "
                        "expected 'engineering_objective'"
                    ),
                }
            readiness = self.hierarchy.evaluate_release_readiness(objective_id)
            try:
                schedule = self.hierarchy.plan_execution_tranches(objective_id)
                schedule_payload: dict[str, Any] | None = schedule.model_dump(mode="json")
            except PermissionError:
                schedule_payload = None
        except KeyError as exc:
            return 404, {"error": "not_found", "detail": str(exc)}
        except ValueError as exc:
            return 422, {"error": "invalid_objective", "detail": str(exc)}

        return 200, {
            "objective": node.model_dump(mode="json"),
            "schedule": schedule_payload,
            "release_readiness": readiness.model_dump(mode="json"),
        }

    def list_domain_packs(self) -> tuple[int, dict[str, Any]]:
        """List registered Scope B domains and persisted domain proposal records."""
        if self.store is None or self.domain_runner is None:
            return self._service_unavailable()

        domains = [domain.value for domain in self.domain_runner.registry.list_domains()]
        proposals = [
            record.model_dump(mode="json")
            for record in self.store.list_domain_proposal_records()
        ]
        return 200, {"domains": domains, "proposals": proposals}

    def evaluate_domain_pack_proposal(self, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        """Validate and execute a Scope B DomainExecutionRequest."""
        if self.store is None or self.domain_runner is None:
            return self._service_unavailable()

        try:
            req = DomainExecutionRequest(**body)
            res = self.domain_runner.execute(req)
        except PermissionError as exc:
            return 403, {"error": "forbidden", "detail": str(exc)}
        except (ValidationError, ValueError, KeyError, TypeError) as exc:
            return 422, {"error": "invalid_domain_proposal", "detail": str(exc)}

        return 200, res.model_dump(mode="json")

    def list_remote_workers(self, *, role: str | None = None) -> tuple[int, dict[str, Any]]:
        """Return active remote worker pool policy and persisted remote workers."""
        if self.store is None or self.remote_coordinator is None:
            return self._service_unavailable()

        workers = [
            worker.model_dump(mode="json")
            for worker in self.store.list_remote_workers(role=role)
        ]
        return 200, {
            "policy": self.remote_coordinator.policy.model_dump(mode="json"),
            "workers": workers,
        }

    def admit_remote_worker(self, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        """Evaluate and optionally persist an operator-hosted remote worker admission request."""
        if self.store is None or self.remote_coordinator is None:
            return self._service_unavailable()

        try:
            if "policy" in body and body["policy"] is not None:
                policy = RemoteWorkerPoolPolicy.model_validate(body["policy"])
                self.remote_coordinator = RemoteWorkerCoordinator(
                    policy=policy,
                    store=self.store,
                )

            if "worker" in body:
                raw_worker = body["worker"]
                if not isinstance(raw_worker, dict):
                    raise ValueError("'worker' must be an object")
                worker_input = raw_worker
            else:
                worker_input = {
                    key: value
                    for key, value in body.items()
                    if key not in {"operator_intent_ref", "policy"}
                }

            worker = RemoteWorkerIdentity(**worker_input)
            operator_intent_ref = body.get("operator_intent_ref")
            if operator_intent_ref is not None and not isinstance(operator_intent_ref, str):
                raise ValueError("'operator_intent_ref' must be a string when provided")
        except (ValidationError, ValueError, TypeError) as exc:
            return 422, {"error": "invalid_remote_worker", "detail": str(exc)}

        admitted, reasons = self.remote_coordinator.register_worker(
            worker,
            operator_intent_ref=operator_intent_ref,
        )
        return (201 if admitted else 403), {
            "admitted": admitted,
            "reasons": reasons,
            "worker": worker.model_dump(mode="json") if admitted else None,
        }

    def record_transfer_manifest(self, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        """Validate and persist an ArtifactTransferManifest."""
        if self.store is None:
            return self._service_unavailable()

        try:
            manifest = ArtifactTransferManifest(**body)
        except (ValidationError, ValueError, TypeError) as exc:
            return 422, {"error": "invalid_transfer_manifest", "detail": str(exc)}

        saved = self.store.save_artifact_transfer_manifest(manifest)
        return 201, {"manifest": saved.model_dump(mode="json")}

    def list_transfer_manifests(
        self,
        *,
        run_id: str | None = None,
        work_item_id: str | None = None,
    ) -> tuple[int, dict[str, Any]]:
        """List persisted ArtifactTransferManifest records."""
        if self.store is None:
            return self._service_unavailable()

        manifests = [
            manifest.model_dump(mode="json")
            for manifest in self.store.list_artifact_transfer_manifests(
                run_id=run_id,
                work_item_id=work_item_id,
            )
        ]
        return 200, {"manifests": manifests}

    def remediate_operation_incident(self, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        """Execute Stage 04 ContinuousVerifiedOperationsWorkflow from an incident trigger."""
        if self.store is None:
            return self._service_unavailable()

        try:
            req = ContinuousOperationsRequest(**body)
            workflow = ContinuousVerifiedOperationsWorkflow(store=self.store)
            res = workflow.execute(req)
        except PermissionError as exc:
            return 403, {"error": "forbidden", "detail": str(exc)}
        except (ValidationError, ValueError, KeyError, TypeError) as exc:
            return 422, {"error": "invalid_operations_request", "detail": str(exc)}

        return 200, res.model_dump(mode="json")
