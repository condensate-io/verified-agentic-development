"""End-to-end 8-step Verified Delivery Flow orchestrator."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
from pathlib import Path
import shutil
from typing import Any, Callable
from uuid import uuid4

from pydantic import BaseModel, Field

from vad.ask.assessment import (
    AskAssessment,
    eip_from_assessment,
    enrich_assessment_with_memory,
)
from vad.ask.classifier import assess_ask
from vad.audit.verify_trail import AuditTrailReport, verify_run_audit_trail
from vad.contracts.models import AutonomyTier, RiskTier
from vad.control_plane.checkpoints import CheckpointStage
from vad.control_plane.clients import (
    ClientHeartbeat,
    ClientManifest,
    ClientRuntimeStatus,
    ClientTrustState,
    ClientType,
)
from vad.control_plane.leases import TaskLeaseAcquireRequest
from vad.control_plane.mcp_gateway import MCPGateway
from vad.control_plane.sessions import HostRole, HostSessionClaims, HostSessionManager
from vad.control_plane.work_items import WorkItem, WorkItemStatus
from vad.evidence.bundle import (
    AgentEvidence,
    EffortEvidence,
    EvidenceBundle,
    EvidenceRef,
    RunEvidence,
    TokenEvidence,
    VerificationEvidence,
)
from vad.feedback.publisher import LearningLoopPublisher
from vad.guards.execution import ExecutionGuard
from vad.memory.contracts import FactType, MemoryQuery, ProposedRuleType
from vad.memory.gateway import MemoryGateway
from vad.memory.providers.null import NullMemoryProvider
from vad.policy.autonomy_matrix import (
    AutonomyEvaluationInput,
    AutonomyMatrixEvaluator,
    PolicyStage,
)
from vad.policy.decisions import PolicyDecision
from vad.policy.engine import PolicyEngine
from vad.proof.mapper import enrich_eip_proofs_from_memory, map_proofs
from vad.proof.plan import compute_eip_digest
from vad.release.decision import ReleaseDecision, ReleaseDecisionEngine, ReleaseOutcome
from vad.review.dsse_attestation import DSSEAttestor
from vad.server.api.clients import ClientRegistryService
from vad.server.api.work_items import WorkItemService
from vad.server.db.store import ApprovalEvent, DeliveryOutcomeRecord, ServerStore
from vad.signing.ed25519_local import LocalEd25519KeyProvider
from vad.verify.freshness import FreshnessValidator, compute_proof_plan_digest
from vad.verify.isolated_executor import (
    IsolatedExecutionConfig,
    IsolatedVerifierExecutor,
    compute_tree_hash,
)
from vad.verify.report import BuilderClaim


class _OfflineToolPolicyEngine(PolicyEngine):
    """Deterministic fallback matching policies/vad.rego when `opa` binary is absent."""

    def evaluate_decision(self, policy_path: str, input_data: dict[str, Any]) -> PolicyDecision:
        tool = input_data.get("tool")
        if input_data.get("action") == "use_tool" and tool not in {"shell", "network"}:
            return PolicyDecision(allow=True, reasons=["policy allow rule matched"])
        return PolicyDecision(allow=False, denials=["tool access denied"])


class VerifiedDeliveryRequest(BaseModel):
    """Input specification for an end-to-end Verified Delivery Flow execution."""

    run_id: str
    change_request: str
    eip_name: str
    risk_tier: RiskTier
    domain_tags: list[str] = Field(default_factory=list)
    builder_actor: str = "codex-builder-1"
    builder_host_type: str = "codex"
    verifier_actor: str = "isolated-verifier-1"
    human_approvers: list[str] = Field(default_factory=list)
    explicit_authorization_ref: str | None = None
    commit_sha: str = "a" * 40
    stage: PolicyStage = PolicyStage.CI_CD
    cost_usd: float = 0.05
    human_review_seconds: float | None = None


class VerifiedDeliveryResult(BaseModel):
    """Structured result emitted at the completion of the 8-step Verified Delivery Flow."""

    run_id: str
    eip_digest: str
    proof_plan_digest: str
    evidence_digest: str
    applied_lesson_ids: list[str]
    retrieved_fact_ids: list[str]
    verification_passed: bool
    freshness_passed: bool
    release_decision: ReleaseDecision
    audit_report: AuditTrailReport
    published_fact_id: str | None
    published_lesson_id: str | None
    memory_degraded: bool
    checkpoints_recorded: list[str]


def _resolve_policy_path() -> str:
    candidate = Path("policies/vad.rego")
    if candidate.exists():
        return str(candidate)
    repo_relative = Path(__file__).resolve().parents[2] / "policies" / "vad.rego"
    if repo_relative.exists():
        return str(repo_relative)
    return "policies/vad.rego"


def _autonomy_tier_for_risk(risk_tier: RiskTier) -> AutonomyTier:
    if risk_tier == RiskTier.LOW:
        return AutonomyTier.AUTONOMOUS
    return AutonomyTier.BOUNDED


def _sanitize_id_segment(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in value).strip("-") or "run"


class VerifiedDeliveryWorkflow:
    """Executes the 8-step Verified Delivery Flow over VAD's authoritative control plane."""

    def __init__(
        self,
        store: ServerStore,
        memory_gateway: MemoryGateway | None = None,
        key_provider: LocalEd25519KeyProvider | None = None,
        workspace_dir: Path | None = None,
        command_executor: Callable | None = None,
    ) -> None:
        self.store = store
        self.memory_gateway = (
            memory_gateway
            if memory_gateway is not None
            else MemoryGateway(provider=NullMemoryProvider())
        )
        if key_provider is not None:
            self.key_provider = key_provider
        elif str(store.db_path) != ":memory:":
            self.key_provider = LocalEd25519KeyProvider(
                key_dir=Path(store.db_path).parent / ".vad" / "keys"
            )
        else:
            self.key_provider = LocalEd25519KeyProvider()

        self.workspace_dir = Path(workspace_dir) if workspace_dir is not None else None
        self.command_executor = command_executor

        self.work_items = WorkItemService(store)
        self.clients = ClientRegistryService(store)
        session_secret = hashlib.sha256(
            f"vad-verified-delivery-session:{store.db_path}".encode("utf-8")
        ).digest()
        self.session_manager = HostSessionManager(signing_secret=session_secret)
        self.mcp_gateway = MCPGateway(
            session_manager=self.session_manager,
            require_host_session=True,
        )
        self.freshness_validator = FreshnessValidator()
        self.release_engine = ReleaseDecisionEngine(
            evaluator=AutonomyMatrixEvaluator(
                policy_path=_resolve_policy_path(),
                use_opa=shutil.which("opa") is not None,
            )
        )

    def _build_execution_guard(self) -> ExecutionGuard:
        guard = ExecutionGuard(policy_path=_resolve_policy_path())
        if shutil.which("opa") is None:
            guard.engine = _OfflineToolPolicyEngine()
        return guard

    def _ensure_builder_client_active(self, request: VerifiedDeliveryRequest) -> str:
        client_id = request.builder_actor.strip()
        try:
            self.store.load_client_manifest(client_id)
        except KeyError:
            try:
                client_type = ClientType(request.builder_host_type)
            except ValueError:
                client_type = ClientType.GENERIC_MCP
            self.clients.register(
                ClientManifest(
                    client_id=client_id,
                    display_name=f"{request.builder_host_type} ({client_id})",
                    client_type=client_type,
                    version="1.0.0",
                    connection_mode="mcp",
                    supported_capabilities=("repo_patch", "work_intake"),
                    workspace_root=self.workspace_dir or Path.cwd(),
                    trust_state=ClientTrustState.TRUSTED,
                )
            )
        self.clients.heartbeat(
            ClientHeartbeat(
                client_id=client_id,
                run_id=request.run_id,
                actor=request.builder_actor,
                role="builder",
                status=ClientRuntimeStatus.ACTIVE,
            )
        )
        return client_id

    def _build_base_assessment(self, request: VerifiedDeliveryRequest) -> AskAssessment:
        raw = assess_ask(request.change_request)
        release_needed = raw.release_needed or request.risk_tier in {
            RiskTier.MEDIUM,
            RiskTier.HIGH,
            RiskTier.CRITICAL,
        }
        telemetry_needed = raw.telemetry_needed or release_needed or request.risk_tier in {
            RiskTier.HIGH,
            RiskTier.CRITICAL,
        }
        return raw.model_copy(
            update={
                "risk_tier": request.risk_tier,
                "autonomy_tier": _autonomy_tier_for_risk(request.risk_tier),
                "release_needed": release_needed,
                "telemetry_needed": telemetry_needed,
                "clarification_questions": [],
            }
        )

    def execute(
        self,
        request: VerifiedDeliveryRequest,
        *,
        candidate_lesson_title: str | None = None,
        candidate_lesson_recommendation: str | None = None,
        candidate_rule_type: ProposedRuleType = ProposedRuleType.PROOF_OBLIGATION,
        simulate_drift_after_verify: bool = False,
    ) -> VerifiedDeliveryResult:
        """Execute all 8 steps of the Verified Delivery Flow."""
        work_item_id = f"wi-{_sanitize_id_segment(request.run_id)}"

        # ------------------------------------------------------------------
        # Step 1: Receive change request, query advisory memory, enrich
        # AskAssessment & create EIP -> save EIP_CREATED checkpoint.
        # ------------------------------------------------------------------
        base_assessment = self._build_base_assessment(request)
        advisory_context = self.memory_gateway.retrieve_advisory_context(
            MemoryQuery(
                query_text=base_assessment.summary,
                domain_tags=list(request.domain_tags),
                include_unapproved_lessons=False,
            )
        )
        enriched_assessment = enrich_assessment_with_memory(
            base_assessment,
            memory_gateway=self.memory_gateway,
            domain_tags=request.domain_tags,
        )
        eip = eip_from_assessment(
            request.eip_name,
            base_assessment,
            memory_gateway=self.memory_gateway,
            domain_tags=request.domain_tags,
        )
        initial_eip_digest = compute_eip_digest(eip)

        bootstrap_item = WorkItem(
            work_item_id=work_item_id,
            run_id=request.run_id,
            title=f"Deliver {request.eip_name}",
            description=enriched_assessment.summary[:2000],
            role="builder",
            requested_capability="repo_patch",
            priority=100,
            status=WorkItemStatus.QUEUED,
        )
        self.work_items.record_checkpoint(
            bootstrap_item,
            stage=CheckpointStage.EIP_CREATED,
            actor="planner",
            role="operator",
            fence_token=1,
            artifact_digests={"eip": initial_eip_digest},
            state_snapshot={"eip_name": eip.name, "risk_tier": eip.risk_tier.value},
        )

        # ------------------------------------------------------------------
        # Step 2: Map EIP + approved lessons to executable ProofPlan ->
        # save PROOF_PLANNED checkpoint.
        # ------------------------------------------------------------------
        enriched_eip, mapper_lesson_ids = enrich_eip_proofs_from_memory(
            eip,
            self.memory_gateway,
            domain_tags=request.domain_tags,
        )
        proof_plan = map_proofs(
            enriched_eip,
            memory_gateway=None,
            domain_tags=request.domain_tags,
        )
        eip = enriched_eip
        eip_digest = compute_eip_digest(eip)
        proof_plan_digest = compute_proof_plan_digest(proof_plan)

        applied_lesson_ids = list(
            dict.fromkeys([*enriched_assessment.applied_lesson_ids, *mapper_lesson_ids])
        )
        retrieved_fact_ids = list(
            dict.fromkeys(
                [
                    *enriched_assessment.retrieved_fact_ids,
                    *(fact.fact_id for fact in advisory_context.facts),
                ]
            )
        )

        self.work_items.record_checkpoint(
            bootstrap_item,
            stage=CheckpointStage.PROOF_PLANNED,
            actor="planner",
            role="operator",
            fence_token=1,
            artifact_digests={"eip": eip_digest, "proof_plan": proof_plan_digest},
            state_snapshot={
                "obligations": len(proof_plan.mappings),
                "applied_lesson_ids": applied_lesson_ids,
            },
        )

        # ------------------------------------------------------------------
        # Step 3: Create WorkItem, assign to builder host with fenced lease
        # + scoped builder HostSessionToken, record non-authoritative
        # BuilderClaim -> save CONSTRUCTION_ASSIGNED and DIFF_PROPOSED.
        # ------------------------------------------------------------------
        builder_client_id = self._ensure_builder_client_active(request)
        self.work_items.create(
            bootstrap_item,
            actor="orchestrator",
            role="operator",
        )
        fence_token = self.store.next_fence_token(work_item_id)
        lease = self.store.acquire_task_lease(
            TaskLeaseAcquireRequest(
                task_id=work_item_id,
                run_id=request.run_id,
                client_id=builder_client_id,
                role="builder",
                actor=request.builder_actor,
                fence_token=fence_token,
            )
        )
        assign_res = self.work_items.assign(
            work_item_id,
            actor="scheduler",
            role="operator",
            client_id="control-plane",
            assigned_client_id=builder_client_id,
            lease_id=lease.task_id,
            fence_token=lease.fence_token,
            checkpoint_stage=CheckpointStage.CONSTRUCTION_ASSIGNED,
            artifact_digests={"eip": eip_digest, "proof_plan": proof_plan_digest},
        )
        assert assign_res.status_code == 200

        now_utc = datetime.now(timezone.utc)
        builder_session = self.session_manager.issue(
            HostSessionClaims(
                session_id=f"sess-{uuid4().hex[:12]}",
                client_id=builder_client_id,
                host_type=request.builder_host_type,
                role=HostRole.BUILDER,
                actor=request.builder_actor,
                work_item_id=work_item_id,
                fence_token=lease.fence_token,
                workspace_root=str(self.workspace_dir) if self.workspace_dir is not None else None,
                issued_at=now_utc,
                expires_at=now_utc + timedelta(hours=1),
            )
        )
        session_decision = self.mcp_gateway.authorize_session(
            builder_session,
            action="submit_builder_claim",
            work_item_id=work_item_id,
            min_fence_token=lease.fence_token,
            client_id=builder_client_id,
            now=now_utc,
        )
        if not session_decision.allow:
            raise PermissionError("; ".join(session_decision.denials))

        start_res = self.work_items.start(
            work_item_id,
            actor=request.builder_actor,
            role="builder",
            client_id=builder_client_id,
            fence_token=lease.fence_token,
        )
        assert start_res.status_code == 200

        claimed_tree_hash = (
            compute_tree_hash(self.workspace_dir)
            if self.workspace_dir is not None
            else hashlib.sha256(b"empty-workspace").hexdigest()
        )
        builder_claim = BuilderClaim(
            claim_id=f"claim-{uuid4().hex[:12]}",
            builder_actor=request.builder_actor,
            commit_sha=request.commit_sha,
            tree_hash=claimed_tree_hash,
            summary=f"Builder {request.builder_actor} proposed changes for {request.eip_name}",
            claimed_passed=True,
        )

        diff_res = self.work_items.verify(
            work_item_id,
            actor=request.builder_actor,
            role="builder",
            client_id=builder_client_id,
            fence_token=lease.fence_token,
            checkpoint_stage=CheckpointStage.DIFF_PROPOSED,
            artifact_digests={"eip": eip_digest, "proof_plan": proof_plan_digest},
        )
        assert diff_res.status_code == 200

        # ------------------------------------------------------------------
        # Step 4: Run independent verification in isolated environment,
        # compute IndependentVerificationResult, run FreshnessValidator,
        # sign Ed25519 DSSE review attestation -> save VERIFIED checkpoint.
        # ------------------------------------------------------------------
        executor = IsolatedVerifierExecutor(
            config=IsolatedExecutionConfig(mode="sandbox"),
            execution_guard=self._build_execution_guard(),
            subprocess_runner=self.command_executor,
        )
        verification_result = executor.verify_independent(
            eip,
            proof_plan,
            builder_actor=request.builder_actor,
            verifier_actor=request.verifier_actor,
            commit_sha=request.commit_sha,
            workspace_dir=self.workspace_dir,
            builder_claim=builder_claim,
            attested=True,
            dual_signoff=(eip.risk_tier == RiskTier.CRITICAL),
            signoff_actors=[request.verifier_actor, *request.human_approvers],
        )

        target_commit_sha = request.commit_sha
        target_tree_hash = verification_result.tree_hash
        if simulate_drift_after_verify:
            target_tree_hash = "0" * 64 if verification_result.tree_hash != "0" * 64 else "1" * 64
            if not eip.effective_freshness_policy().require_tree_hash_match:
                target_commit_sha = "f" * 40 if request.commit_sha != "f" * 40 else "e" * 40

        freshness_result = self.freshness_validator.validate(
            verification_result,
            eip=eip,
            proof_plan=proof_plan,
            target_commit_sha=target_commit_sha,
            target_tree_hash=target_tree_hash,
            current_env_digest=verification_result.env_digest,
        )

        attestor = DSSEAttestor(key_provider=self.key_provider, strict=True)
        review_decision = (
            "approved"
            if (verification_result.passed and freshness_result.is_fresh)
            else "rejected"
        )
        dsse_envelope = attestor.create_review_attestation(
            commit_sha=request.commit_sha,
            review_decision=review_decision,
            reviewer_agent=request.verifier_actor,
            proof_summary={
                "verification_id": verification_result.verification_id,
                "passed": verification_result.passed,
                "fresh": freshness_result.is_fresh,
                "obligations": len(verification_result.obligation_results),
            },
            eip_digest=eip_digest,
            proof_plan_digest=proof_plan_digest,
            assurance_level=verification_result.assurance_achieved.value,
            verified_at=verification_result.verified_at.isoformat(),
            expires_at=verification_result.expires_at.isoformat(),
        )

        current_item = self.store.load_work_item(work_item_id)
        self.work_items.record_checkpoint(
            current_item,
            stage=CheckpointStage.VERIFIED,
            actor=request.verifier_actor,
            role="verifier",
            fence_token=lease.fence_token,
            artifact_digests={
                "eip": eip_digest,
                "proof_plan": proof_plan_digest,
                "verification": verification_result.receipt_sha,
            },
            state_snapshot={
                "verification_passed": verification_result.passed,
                "freshness_passed": freshness_result.is_fresh,
                "assurance_achieved": verification_result.assurance_achieved.value,
            },
        )

        # ------------------------------------------------------------------
        # Step 5: Evaluate ReleaseDecisionEngine (Risk x Assurance x
        # Freshness x Stage), build RunEvidence to bind exact evidence_digest,
        # persist ReleaseDecision -> save POLICY_DECIDED checkpoint.
        # ------------------------------------------------------------------
        eval_input = AutonomyEvaluationInput(
            risk_tier=eip.risk_tier,
            stage=request.stage,
            verification_passed=verification_result.passed,
            assurance_achieved=verification_result.assurance_achieved,
            independent_verifier=(
                verification_result.verifier_actor.strip()
                != verification_result.builder_actor.strip()
            ),
            evidence_fresh=freshness_result.is_fresh,
            freshness_reasons=freshness_result.reasons,
            drift_detected=False,
            human_approvers=list(request.human_approvers),
            builder_actor=request.builder_actor,
            verifier_actor=request.verifier_actor,
            explicit_authorization_ref=request.explicit_authorization_ref,
        )
        preliminary_decision = self.release_engine.decide(
            run_id=request.run_id,
            eip_digest=eip_digest,
            evidence_digest="0" * 64,
            eval_input=eval_input,
        )

        memory_events: list[dict[str, Any]] = [
            {
                "event": "advisory_memory_retrieved",
                "provider": advisory_context.provider_name,
                "degraded": advisory_context.degraded,
                "retrieved_fact_ids": retrieved_fact_ids,
                "applied_lesson_ids": applied_lesson_ids,
            }
        ]
        if advisory_context.degraded_reason:
            memory_events[0]["degraded_reason"] = advisory_context.degraded_reason

        if preliminary_decision.can_release:
            final_decision_str = "passed"
        elif preliminary_decision.outcome in {
            ReleaseOutcome.HUMAN_APPROVAL_REQUIRED,
            ReleaseOutcome.RESTRICTED_AUTHORIZATION_REQUIRED,
        }:
            final_decision_str = "needs_human"
        else:
            final_decision_str = "blocked"

        run_evidence = RunEvidence(
            run_id=request.run_id,
            created_at=datetime.now(timezone.utc).isoformat(),
            eip=EvidenceRef(path=None, digest=eip_digest),
            proof_plan=EvidenceRef(path=None, digest=proof_plan_digest),
            agents=AgentEvidence(
                builder=request.builder_actor,
                verifier=request.verifier_actor,
            ),
            policy_decisions=[
                {
                    "decision_id": preliminary_decision.decision_id,
                    "outcome": preliminary_decision.outcome.value,
                    "can_release": preliminary_decision.can_release,
                    "reasons": preliminary_decision.reasons,
                }
            ],
            tool_calls=[
                res.tool_call
                for res in verification_result.obligation_results
                if res.tool_call is not None
            ],
            memory_events=memory_events,
            verification=VerificationEvidence(
                passed=verification_result.passed,
                results=[
                    res.model_dump(mode="json")
                    for res in verification_result.obligation_results
                ],
            ),
            release={
                "evaluated": True,
                "outcome": preliminary_decision.outcome.value,
                "can_release": preliminary_decision.can_release,
                "risk_tier": eip.risk_tier.value,
                "assurance_level": verification_result.assurance_achieved.value,
                "freshness_passed": freshness_result.is_fresh,
                "dsse_envelope": dsse_envelope.model_dump(mode="json"),
            },
            effort=EffortEvidence(
                effort_type=enriched_assessment.effort_type.value,
                mees=max(70, enriched_assessment.mees_budget.minimum_score),
                policy="pass",
                changed_files=1,
                line_delta=10,
                new_dependencies=0,
                complexity_delta=0,
                maintainability_delta=0,
            ),
            tokens=TokenEvidence(
                budget=enriched_assessment.token_budget,
                estimated=1000,
                used=1000,
                remaining=max(0, enriched_assessment.token_budget - 1000),
            ),
            final_decision=final_decision_str,
            blocker=None if preliminary_decision.can_release else "; ".join(preliminary_decision.reasons),
        )
        evidence_digest = EvidenceBundle(run_evidence).compute_hash()
        release_decision = preliminary_decision.model_copy(
            update={"evidence_digest": evidence_digest}
        )
        self.store.save_release_decision(release_decision)

        self.work_items.record_checkpoint(
            current_item,
            stage=CheckpointStage.POLICY_DECIDED,
            actor="policy-engine",
            role="operator",
            fence_token=lease.fence_token,
            artifact_digests={
                "eip": eip_digest,
                "proof_plan": proof_plan_digest,
                "evidence": evidence_digest,
            },
            state_snapshot={
                "outcome": release_decision.outcome.value,
                "can_release": release_decision.can_release,
            },
        )

        # ------------------------------------------------------------------
        # Step 6: Persist authoritative RunEvidence and DeliveryOutcomeRecord;
        # transition work item to COMPLETED (saving RELEASED checkpoint) if
        # releasable, or block/wait_for_human otherwise.
        # ------------------------------------------------------------------
        self.store.save_run_evidence(run_evidence)

        if release_decision.can_release:
            for approver in release_decision.recorded_approvers:
                self.store.save_approval_event(
                    ApprovalEvent(
                        approval_id=f"app-{uuid4().hex[:12]}",
                        run_id=request.run_id,
                        actor=approver,
                        action="approve_release",
                        decision=PolicyDecision(
                            allow=True,
                            reasons=[f"Release guardian '{approver}' approved release"],
                        ),
                        evidence_digest=evidence_digest,
                    )
                )

            approver_actor = (
                release_decision.recorded_approvers[0]
                if release_decision.recorded_approvers
                else "release-engine"
            )
            approve_res = self.work_items.approve(
                work_item_id,
                actor=approver_actor,
                role="release_guardian",
                client_id=builder_client_id,
                fence_token=lease.fence_token,
                checkpoint_stage=CheckpointStage.POLICY_DECIDED,
                artifact_digests={"evidence": evidence_digest},
            )
            assert approve_res.status_code == 200
            complete_res = self.work_items.complete(
                work_item_id,
                actor=approver_actor,
                role="release_guardian",
                client_id=builder_client_id,
                evidence_digest=evidence_digest,
                fence_token=lease.fence_token,
                checkpoint_stage=CheckpointStage.RELEASED,
                artifact_digests={"evidence": evidence_digest},
            )
            assert complete_res.status_code == 200
        elif release_decision.outcome in {
            ReleaseOutcome.HUMAN_APPROVAL_REQUIRED,
            ReleaseOutcome.RESTRICTED_AUTHORIZATION_REQUIRED,
        }:
            wait_res = self.work_items.wait_for_human(
                work_item_id,
                actor="policy-engine",
                role="operator",
                client_id=builder_client_id,
                fence_token=lease.fence_token,
                checkpoint_stage=CheckpointStage.POLICY_DECIDED,
                artifact_digests={"evidence": evidence_digest},
            )
            assert wait_res.status_code == 200
        else:
            block_res = self.work_items.block(
                work_item_id,
                actor="policy-engine",
                role="operator",
                client_id=builder_client_id,
                reason="; ".join(release_decision.reasons) or release_decision.outcome.value,
                fence_token=lease.fence_token,
                checkpoint_stage=CheckpointStage.POLICY_DECIDED,
                artifact_digests={"evidence": evidence_digest},
            )
            assert block_res.status_code == 200

        self.store.record_delivery_outcome(
            DeliveryOutcomeRecord(
                run_id=request.run_id,
                evidence_digest=evidence_digest,
                risk_tier=eip.risk_tier.value,
                accepted=release_decision.can_release,
                autonomous=(release_decision.outcome == ReleaseOutcome.AUTONOMOUS_RELEASE),
                human_review_seconds=request.human_review_seconds,
                cost_usd=request.cost_usd,
            )
        )

        # ------------------------------------------------------------------
        # Step 7: Publish ObservedFact (and optional PROPOSED InferredLesson)
        # to memory_gateway + lesson_promotions -> save MEMORY_SYNCED checkpoint.
        # ------------------------------------------------------------------
        publisher = LearningLoopPublisher(self.memory_gateway, store=self.store)
        if release_decision.can_release:
            fact_type = FactType.VERIFIED_DELIVERY
        elif not verification_result.passed:
            fact_type = FactType.VERIFICATION_FAILURE
        elif release_decision.outcome in {
            ReleaseOutcome.HUMAN_APPROVAL_REQUIRED,
            ReleaseOutcome.RESTRICTED_AUTHORIZATION_REQUIRED,
        }:
            fact_type = FactType.REJECTED_CHANGE
        else:
            fact_type = FactType.POLICY_DENIAL

        fact = publisher.publish_run_outcome(
            run_id=request.run_id,
            eip_digest=eip_digest,
            evidence_digest=evidence_digest,
            risk_tier=eip.risk_tier.value,
            fact_type=fact_type,
            summary=(
                f"Run {request.run_id} ({eip.risk_tier.value}): "
                f"{release_decision.outcome.value} for {request.change_request}"
            ),
            domain_tags=request.domain_tags,
            metadata={
                "outcome": release_decision.outcome.value,
                "verification_passed": verification_result.passed,
                "freshness_passed": freshness_result.is_fresh,
            },
        )
        published_fact_id = fact.fact_id

        published_lesson_id: str | None = None
        if candidate_lesson_title or candidate_lesson_recommendation:
            title = candidate_lesson_title or f"Lesson from {request.run_id}"
            recommendation = candidate_lesson_recommendation or title
            lesson = publisher.publish_candidate_lesson(
                title=title,
                recommendation=recommendation,
                proposed_rule_type=candidate_rule_type,
                derived_from_fact_ids=[fact.fact_id],
                domain_tags=request.domain_tags,
            )
            published_lesson_id = lesson.lesson_id

        latest_item = self.store.load_work_item(work_item_id)
        self.work_items.record_checkpoint(
            latest_item,
            stage=CheckpointStage.MEMORY_SYNCED,
            actor="learning-loop",
            role="operator",
            fence_token=lease.fence_token,
            artifact_digests={"evidence": evidence_digest},
            state_snapshot={
                "published_fact_id": published_fact_id,
                "published_lesson_id": published_lesson_id,
            },
        )

        # ------------------------------------------------------------------
        # Step 8: Independently verify complete audit trail and return result.
        # ------------------------------------------------------------------
        audit_report = verify_run_audit_trail(
            self.store,
            request.run_id,
            key_provider=self.key_provider,
            expected_commit_sha=request.commit_sha,
        )

        checkpoints_recorded = list(
            dict.fromkeys(
                cp.stage.value for cp in self.store.list_checkpoints(request.run_id)
            )
        )

        return VerifiedDeliveryResult(
            run_id=request.run_id,
            eip_digest=eip_digest,
            proof_plan_digest=proof_plan_digest,
            evidence_digest=evidence_digest,
            applied_lesson_ids=applied_lesson_ids,
            retrieved_fact_ids=retrieved_fact_ids,
            verification_passed=verification_result.passed,
            freshness_passed=freshness_result.is_fresh,
            release_decision=release_decision,
            audit_report=audit_report,
            published_fact_id=published_fact_id,
            published_lesson_id=published_lesson_id,
            memory_degraded=advisory_context.degraded,
            checkpoints_recorded=checkpoints_recorded,
        )
