from datetime import datetime
from uuid import uuid4

from vad.contracts.models import EIP
from vad.evidence.bundle import (
    AgentEvidence,
    EffortEvidence,
    EvidenceRef,
    RunEvidence,
    SignatureEvidence,
    SignatureVerificationEvidence,
    SignedEvidence,
    TokenEvidence,
    VerificationEvidence,
)
from vad.effort.scoring import DiffMetrics, EffortScore
from vad.proof.plan import ProofPlan, compute_eip_digest
from vad.router.models import TokenBudget
from vad.router.privacy import redact_provider_payload, redaction_digest
from vad.signing.models import SignatureEnvelope
from vad.verify.report import VerifierReport


class EvidenceRecorder:
    def create_run_evidence(
        self,
        eip: EIP,
        proof_plan: ProofPlan,
        builder: str,
        verifier: str,
        final_decision: str,
        policy_decisions: list[dict],
        verification: VerifierReport | None = None,
        blocker: str | None = None,
        effort_score: EffortScore | None = None,
        effort_metrics: DiffMetrics | None = None,
        token_budget: TokenBudget | None = None,
        model_routes: list[dict] | None = None,
        release: dict | None = None,
    ) -> RunEvidence:
        return RunEvidence(
            run_id=str(uuid4()),
            created_at=datetime.utcnow().isoformat(),
            eip=EvidenceRef(digest=compute_eip_digest(eip)),
            proof_plan=EvidenceRef(digest=proof_plan.eip_digest),
            agents=AgentEvidence(builder=builder, verifier=verifier),
            policy_decisions=policy_decisions,
            tool_calls=_tool_call_events(verification),
            model_routes=model_routes if model_routes is not None else _model_route_events(eip),
            memory_events=_memory_events(eip),
            verification=_verification_evidence(verification),
            release=release if release is not None else _release_event(eip, final_decision),
            effort=_effort_evidence(effort_score, effort_metrics),
            tokens=_token_evidence(eip, token_budget),
            final_decision=final_decision,
            blocker=blocker,
        )

    def create_signed_evidence(self, payload: dict, envelope: SignatureEnvelope) -> SignedEvidence:
        return SignedEvidence(
            payload=payload,
            signature=SignatureEvidence(
                key_id=envelope.key_id,
                algorithm=envelope.algorithm.value,
                payload_digest=envelope.payload_digest,
                created_at=envelope.created_at.isoformat(),
            ),
        )

    def create_signature_verification_evidence(
        self,
        envelope: SignatureEnvelope,
        verified: bool,
        denial: str | None = None,
    ) -> SignatureVerificationEvidence:
        return SignatureVerificationEvidence(
            key_id=envelope.key_id,
            payload_digest=envelope.payload_digest,
            verified=verified,
            denial=denial,
        )

    def create_provider_call_evidence(self, provider: str, request: dict, response: dict | None = None) -> dict:
        return {
            "event": "provider_call",
            "provider": provider,
            "request": redact_provider_payload(request),
            "request_redaction_digest": redaction_digest(request),
            "response": redact_provider_payload(response or {}),
        }


def _verification_evidence(report: VerifierReport | None) -> VerificationEvidence | None:
    if report is None:
        return None
    return VerificationEvidence(
        passed=report.passed,
        results=[result.model_dump(mode="json") for result in report.results],
    )


def _effort_evidence(score: EffortScore | None, metrics: DiffMetrics | None) -> EffortEvidence:
    if score is None:
        return EffortEvidence(
            effort_type="feature",
            mees=100,
            policy="pass",
            changed_files=0,
            line_delta=0,
            new_dependencies=0,
            complexity_delta=0,
            maintainability_delta=0,
        )
    return EffortEvidence(
        effort_type=score.effort_type.value,
        mees=score.score,
        policy=score.policy,
        changed_files=metrics.changed_files if metrics else 0,
        line_delta=metrics.line_delta if metrics else 0,
        new_dependencies=metrics.dependency_files_changed if metrics else 0,
        complexity_delta=score.penalties.complexity,
        maintainability_delta=-score.penalties.maintainability,
    )


def _token_evidence(eip: EIP, token_budget: TokenBudget | None) -> TokenEvidence:
    if token_budget is None:
        return TokenEvidence(
            budget=eip.model_budget.max_tokens,
            optimization_notes=["actual token usage unavailable in local reference loop"],
        )
    return TokenEvidence(
        budget=token_budget.budget,
        estimated=token_budget.estimated,
        used=token_budget.used,
        remaining=token_budget.remaining,
        optimization_notes=token_budget.optimization_notes,
    )


def _tool_call_events(report: VerifierReport | None) -> list[dict]:
    if report is None:
        return []
    return [
        {
            "event": "verification_tool_call",
            "obligation_id": result.obligation_id,
            **result.tool_call,
        }
        for result in report.results
        if result.tool_call is not None
    ]


def _model_route_events(eip: EIP) -> list[dict]:
    return [
        {
            "event": "model_budget",
            "max_tokens": eip.model_budget.max_tokens,
            "max_cost": eip.model_budget.max_cost,
            "max_loop_depth": eip.model_budget.max_loop_depth,
            "usage": "unavailable",
        }
    ]


def _memory_events(eip: EIP) -> list[dict]:
    if not eip.memory_requirements:
        return [{"event": "memory_requirements", "count": 0, "requirements": []}]
    return [
        {
            "event": "memory_requirement",
            "scope": requirement.scope,
            "purpose": requirement.purpose,
            "max_payload_size": requirement.max_payload_size,
        }
        for requirement in eip.memory_requirements
    ]


def _release_event(eip: EIP, final_decision: str) -> dict:
    return {
        "event": "release_gate",
        "required": eip.release_requirements.required,
        "gates": eip.release_requirements.gates,
        "decision": "not_required" if not eip.release_requirements.required else final_decision,
    }
