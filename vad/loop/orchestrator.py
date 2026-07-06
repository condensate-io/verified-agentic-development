from pydantic import BaseModel, Field

from vad.contracts.models import EIP
from vad.evidence.recorder import EvidenceRecorder
from vad.effort.scoring import DiffMetrics, EffortScore
from vad.loop.state import LoopState, LoopStatus
from vad.policy.engine import PolicyEngine
from vad.proof.plan import ProofPlan
from vad.release.gates import ReleaseManager, RolloutGate
from vad.router.models import TokenBudget
from vad.router.providers.fake import ProviderCompletionRequest
from vad.verify.runner import VerifierRunner


class LoopResult(BaseModel):
    final_decision: LoopStatus
    state: LoopState
    evidence: dict = Field(default_factory=dict)


class VADOrchestrator:
    def __init__(
        self,
        policy_path: str = "policies/vad.rego",
        effort_score: EffortScore | None = None,
        effort_metrics: DiffMetrics | None = None,
        effort_justification: str | None = None,
        dependency_approval: bool = False,
        release_manager: ReleaseManager | None = None,
        release_metrics: dict[str, float] | None = None,
        provider=None,
        provider_model: str | None = None,
    ):
        self.policy_path = policy_path
        self.policy = PolicyEngine()
        self.recorder = EvidenceRecorder()
        self.effort_score = effort_score
        self.effort_metrics = effort_metrics
        self.effort_justification = effort_justification
        self.dependency_approval = dependency_approval
        self.release_manager = release_manager or ReleaseManager()
        self.release_metrics = release_metrics or {"health": 1.0}
        self.provider = provider
        self.provider_model = provider_model

    def run(self, eip: EIP, proof_plan: ProofPlan, builder: str, verifier: str) -> LoopResult:
        state = LoopState()
        policy_decisions = []
        model_routes = None
        token_budget = None

        effort_blocker = self._effort_blocker()
        if effort_blocker:
            state = state.transition(LoopStatus.BLOCKED)
            evidence = self.recorder.create_run_evidence(
                eip,
                proof_plan,
                builder,
                verifier,
                LoopStatus.BLOCKED.value,
                policy_decisions,
                blocker=effort_blocker,
                effort_score=self.effort_score,
                effort_metrics=self.effort_metrics,
            ).model_dump(mode="json")
            return LoopResult(final_decision=LoopStatus.BLOCKED, state=state, evidence=evidence)

        if eip.model_budget.max_tokens == 0 or eip.model_budget.max_cost == 0:
            state = state.transition(LoopStatus.BLOCKED)
            evidence = self.recorder.create_run_evidence(
                eip,
                proof_plan,
                builder,
                verifier,
                LoopStatus.BLOCKED.value,
                policy_decisions,
                blocker="budget exhausted",
                effort_score=self.effort_score,
                effort_metrics=self.effort_metrics,
            ).model_dump(mode="json")
            return LoopResult(final_decision=LoopStatus.BLOCKED, state=state, evidence=evidence)

        state = state.transition(LoopStatus.PLANNED)
        decision = self.policy.evaluate_decision(
            self.policy_path,
            {"action": "build", "builder": builder, "approver": verifier},
        )
        policy_decisions.append(decision.model_dump(mode="json"))
        if not decision.allow:
            state = state.transition(LoopStatus.POLICY_CHECKED)
            state = state.transition(LoopStatus.NEEDS_HUMAN)
            evidence = self.recorder.create_run_evidence(
                eip,
                proof_plan,
                builder,
                verifier,
                LoopStatus.NEEDS_HUMAN.value,
                policy_decisions,
                effort_score=self.effort_score,
                effort_metrics=self.effort_metrics,
            ).model_dump(mode="json")
            return LoopResult(final_decision=LoopStatus.NEEDS_HUMAN, state=state, evidence=evidence)

        provider_blocker = None
        if self.provider is not None:
            provider_blocker, model_routes, token_budget = self._call_provider(eip)
            if provider_blocker:
                state = state.transition(LoopStatus.BLOCKED)
                evidence = self.recorder.create_run_evidence(
                    eip,
                    proof_plan,
                    builder,
                    verifier,
                    LoopStatus.BLOCKED.value,
                    policy_decisions,
                    blocker=provider_blocker,
                    effort_score=self.effort_score,
                    effort_metrics=self.effort_metrics,
                    token_budget=token_budget,
                    model_routes=model_routes,
                ).model_dump(mode="json")
                return LoopResult(final_decision=LoopStatus.BLOCKED, state=state, evidence=evidence)

        state = state.transition(LoopStatus.POLICY_CHECKED)
        state = state.transition(LoopStatus.VERIFYING)
        report = VerifierRunner(eip, proof_plan).run()

        release_event = None
        final_status = LoopStatus.PASSED if report.passed else LoopStatus.FAILED
        if report.passed and eip.release_requirements.required:
            state = state.transition(LoopStatus.RELEASING)
            release_event, final_status = self._evaluate_release(eip)
        state = state.transition(final_status)
        evidence = self.recorder.create_run_evidence(
            eip,
            proof_plan,
            builder,
            verifier,
            final_status.value,
            policy_decisions,
            verification=report,
            effort_score=self.effort_score,
            effort_metrics=self.effort_metrics,
            token_budget=token_budget,
            model_routes=model_routes,
            release=release_event,
        ).model_dump(mode="json")
        return LoopResult(final_decision=final_status, state=state, evidence=evidence)

    def _effort_blocker(self) -> str | None:
        if self.effort_score and self.effort_score.score < 50:
            return "MEES under 50 requires human review"
        if self.effort_metrics and self.effort_metrics.changed_files > 10 and not self.effort_justification:
            return "changed files over 10 require justification"
        if self.effort_metrics and self.effort_metrics.dependency_files_changed > 0 and not self.dependency_approval:
            return "new dependency requires explicit approval"
        return None

    def _call_provider(self, eip: EIP) -> tuple[str | None, list[dict], TokenBudget]:
        inventory = self.provider.inventory()
        model_name = self.provider_model or inventory.models[0].name
        request = ProviderCompletionRequest(
            prompt=eip.goal.description,
            model=model_name,
            max_output_tokens=min(256, eip.model_budget.max_tokens),
        )
        result = self.provider.complete(request)
        event = result.evidence
        token_budget = TokenBudget.create(eip.model_budget.max_tokens).record_usage(
            result.input_tokens + result.output_tokens
        )
        if not result.allowed:
            return f"provider denied: {result.denial}", [event], token_budget
        if result.estimated_cost > eip.model_budget.max_cost:
            event = {**event, "event": "provider_denied", "denial": "provider cost exceeds budget"}
            return "provider cost exceeds budget", [event], token_budget
        return None, [event], token_budget

    def _evaluate_release(self, eip: EIP) -> tuple[dict, LoopStatus]:
        for gate in eip.release_requirements.gates:
            self.release_manager.add_gate(RolloutGate(metric_name=gate, minimum_health_threshold=0.95))
        try:
            self.release_manager.evaluate_release(
                eip.name,
                metrics=self.release_metrics,
                has_telemetry=eip.telemetry_requirements.required,
            )
            return {
                "event": "release_gate",
                "required": True,
                "gates": eip.release_requirements.gates,
                "decision": "passed",
            }, LoopStatus.PASSED
        except Exception as exc:
            return {
                "event": "release_gate",
                "required": True,
                "gates": eip.release_requirements.gates,
                "decision": "failed",
                "error": str(exc),
            }, LoopStatus.FAILED
