"""End-to-end tests for Scope B DomainPackExecutionRunner workflow."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from vad.adapters.domain_pack import DomainKind
from vad.contracts.models import AssuranceLevel, RiskTier
from vad.memory.contracts import FactType, MemoryQuery
from vad.memory.gateway import MemoryGateway
from vad.memory.providers.local import LocalMemoryProvider
from vad.policy.autonomy_matrix import PolicyStage
from vad.release.decision import ReleaseDecision, ReleaseOutcome
from vad.review.dsse_attestation import DSSEAttestor, decode_attestation_statement
from vad.server.db.store import ServerStore
from vad.signing.ed25519_local import LocalEd25519KeyProvider
from vad.workflows.domain_pack_execution import (
    DomainExecutionRequest,
    DomainExecutionResult,
    DomainPackExecutionRunner,
)

_TEST_SEED = bytes(range(1, 33))


def _make_runner(
    *,
    store: Any | None = None,
    provider: LocalMemoryProvider | None = None,
) -> tuple[DomainPackExecutionRunner, LocalMemoryProvider, LocalEd25519KeyProvider]:
    mem_provider = provider if provider is not None else LocalMemoryProvider()
    gateway = MemoryGateway(provider=mem_provider, fail_open=False)
    key_provider = LocalEd25519KeyProvider.from_seed(_TEST_SEED, key_id="ed25519-scope-b-test")
    runner = DomainPackExecutionRunner(
        store=store,
        memory_gateway=gateway,
        key_provider=key_provider,
    )
    return runner, mem_provider, key_provider


def _verify_dsse_and_decode(
    result: DomainExecutionResult,
    key_provider: LocalEd25519KeyProvider,
    *,
    expected_decision: str,
    expected_reviewer: str,
) -> dict[str, Any]:
    attestor = DSSEAttestor(key_provider=key_provider, strict=True)
    valid, _, err = attestor.verify(result.dsse_envelope)
    assert valid is True, f"DSSE verification failed: {err}"
    assert result.dsse_envelope.signatures[0].keyid == key_provider.key_id

    stmt = decode_attestation_statement(result.dsse_envelope)
    predicate = stmt["predicate"]
    assert predicate["decision"] == expected_decision
    assert predicate["reviewer"] == expected_reviewer
    assert predicate["eip_digest"] == result.proposal.payload_digest
    assert predicate["proof_plan_digest"] == result.release_decision.evidence_digest
    assert predicate["assurance_level"] == result.release_decision.assurance_achieved.value
    return stmt


@pytest.mark.parametrize(
    ("domain", "target_resource", "summary", "payload", "rollback_plan", "expected_tier", "expected_receipt_status"),
    [
        (
            DomainKind.INFRASTRUCTURE,
            "aws_security_group.staging_app",
            "Allow staging VPC subnet ingress",
            {"environment": "staging", "cidr_blocks": ["10.20.0.0/16"]},
            "terraform apply -var-file=previous.tfvars",
            RiskTier.MEDIUM,
            "applied",
        ),
        (
            DomainKind.OPERATIONS,
            "k8s/deployment/read-api",
            "Scale dev read replicas to 3",
            {"environment": "dev", "action_type": "scale_read_replicas"},
            "kubectl scale deployment/read-api --replicas=2",
            RiskTier.LOW,
            "executed",
        ),
        (
            DomainKind.BUSINESS,
            "pricing/tier-pro-annual",
            "Adjust annual promo discount by 5%",
            {"financial_impact_usd": 1200.0, "discount_pct": 10.0},
            "Restore prior pricing table snapshot v2026.10",
            RiskTier.MEDIUM,
            "applied",
        ),
        (
            DomainKind.COMPLIANCE,
            "control/internal-access-review",
            "Record quarterly internal access attestation",
            {"internal_only": True, "audit_ticket": "AUD-2026-101"},
            "Mark attestation superseded and reopen audit ticket",
            RiskTier.MEDIUM,
            "attested",
        ),
    ],
)
def test_autonomous_low_medium_execution_across_all_four_packs(
    domain: DomainKind,
    target_resource: str,
    summary: str,
    payload: dict[str, Any],
    rollback_plan: str,
    expected_tier: RiskTier,
    expected_receipt_status: str,
) -> None:
    runner, mem_provider, key_provider = _make_runner()

    result = runner.execute(
        DomainExecutionRequest(
            run_id=f"run-auto-{domain.value}",
            domain=domain,
            target_resource=target_resource,
            summary=summary,
            payload=payload,
            rollback_plan=rollback_plan,
            builder_actor=f"{domain.value}-builder",
            verifier_actor=f"{domain.value}-verifier",
            domain_tags=["team:platform"],
        )
    )

    assert isinstance(result, DomainExecutionResult)
    assert result.domain == domain
    assert result.validation_errors == []
    assert len(result.proof_outcomes) == 1
    assert result.proof_outcomes[0].passed is True
    assert result.release_decision.risk_tier == expected_tier
    assert result.release_decision.can_release is True
    assert result.release_decision.outcome == ReleaseOutcome.AUTONOMOUS_RELEASE

    assert result.applied is True
    assert result.apply_receipt is not None
    assert result.apply_receipt["status"] == expected_receipt_status
    assert result.apply_receipt["approved_decision_id"] == result.release_decision.decision_id

    _verify_dsse_and_decode(
        result,
        key_provider,
        expected_decision="approved",
        expected_reviewer=f"{domain.value}-verifier",
    )

    assert result.published_fact_id is not None
    facts = mem_provider.list_facts()
    assert len(facts) == 1
    fact = facts[0]
    assert fact.fact_id == result.published_fact_id
    assert fact.fact_type == FactType.VERIFIED_DELIVERY
    assert f"domain:{domain.value}" in fact.domain_tags
    assert "team:platform" in fact.domain_tags
    assert fact.source_evidence_digest == result.release_decision.evidence_digest


def test_auto_apply_false_skips_apply_even_when_approved() -> None:
    runner, _, key_provider = _make_runner()

    result = runner.execute(
        DomainExecutionRequest(
            run_id="run-no-auto-apply",
            domain=DomainKind.OPERATIONS,
            target_resource="service/cache",
            summary="Warm cache in dev",
            payload={"environment": "dev"},
            rollback_plan="Flush warmed cache keys",
            auto_apply_when_approved=False,
        )
    )

    assert result.release_decision.can_release is True
    assert result.applied is False
    assert result.apply_receipt is None
    _verify_dsse_and_decode(
        result,
        key_provider,
        expected_decision="approved",
        expected_reviewer="domain-verifier-1",
    )


def test_high_risk_proposal_blocked_without_human_signoff_and_applied_with_signoff() -> None:
    runner, mem_provider, key_provider = _make_runner()

    blocked = runner.execute(
        DomainExecutionRequest(
            run_id="run-high-blocked",
            domain=DomainKind.INFRASTRUCTURE,
            target_resource="aws_security_group.prod_api",
            summary="Restrict production API ingress CIDR",
            payload={"environment": "production", "cidr_blocks": ["10.0.0.0/16"]},
            rollback_plan="Revert security group rule via Terraform state",
            builder_actor="tf-agent",
            verifier_actor="tf-verifier",
            obligation_ids=["tf-plan-check", "tf-policy-check"],
        )
    )

    assert blocked.release_decision.risk_tier == RiskTier.HIGH
    assert blocked.release_decision.can_release is False
    assert blocked.release_decision.outcome == ReleaseOutcome.HUMAN_APPROVAL_REQUIRED
    assert blocked.applied is False
    assert blocked.apply_receipt is None
    assert len(blocked.proof_outcomes) == 2
    _verify_dsse_and_decode(
        blocked,
        key_provider,
        expected_decision="rejected",
        expected_reviewer="tf-verifier",
    )

    # Builder self-approval is also rejected
    self_approved = runner.execute(
        DomainExecutionRequest(
            run_id="run-high-self-approve",
            domain=DomainKind.INFRASTRUCTURE,
            target_resource="aws_security_group.prod_api",
            summary="Restrict production API ingress CIDR",
            payload={"environment": "production", "cidr_blocks": ["10.0.0.0/16"]},
            rollback_plan="Revert security group rule via Terraform state",
            builder_actor="tf-agent",
            verifier_actor="tf-verifier",
            human_approvers=["tf-agent"],
        )
    )
    assert self_approved.release_decision.can_release is False
    assert self_approved.release_decision.outcome == ReleaseOutcome.DENIED_POLICY
    assert self_approved.applied is False
    assert self_approved.apply_receipt is None

    # Approved once distinct human guardian signs off
    approved = runner.execute(
        DomainExecutionRequest(
            run_id="run-high-approved",
            domain=DomainKind.INFRASTRUCTURE,
            target_resource="aws_security_group.prod_api",
            summary="Restrict production API ingress CIDR",
            payload={"environment": "production", "cidr_blocks": ["10.0.0.0/16"]},
            rollback_plan="Revert security group rule via Terraform state",
            builder_actor="tf-agent",
            verifier_actor="tf-verifier",
            human_approvers=["sre-guardian-1"],
            obligation_ids=["tf-plan-check", "tf-policy-check"],
        )
    )

    assert approved.release_decision.can_release is True
    assert approved.release_decision.outcome == ReleaseOutcome.APPROVED_WITH_HUMAN
    assert approved.release_decision.assurance_achieved == AssuranceLevel.A3_ATTESTED
    assert approved.applied is True
    assert approved.apply_receipt is not None
    assert approved.apply_receipt["approved_decision_id"] == approved.release_decision.decision_id
    _verify_dsse_and_decode(
        approved,
        key_provider,
        expected_decision="approved",
        expected_reviewer="tf-verifier",
    )

    queried = mem_provider.query(
        MemoryQuery(query_text="production API", domain_tags=["domain:infrastructure"])
    )
    assert len(queried.facts) == 3


@pytest.mark.parametrize(
    ("domain", "target_resource", "summary", "payload", "rollback_plan"),
    [
        (
            DomainKind.COMPLIANCE,
            "iam_role/prod-break-glass",
            "Update SOC2-CC6 break-glass policy",
            {"control_family": "SOC2-CC6", "audit_ticket": "AUD-2026-505"},
            "Restore IAM policy version v4",
        ),
        (
            DomainKind.BUSINESS,
            "billing/enterprise-rebate-program",
            "Approve enterprise annual rebate adjustment",
            {"financial_impact_usd": 75000.0, "policy_reference": "FIN-POL-2026-09"},
            "Reverse credit memos and restore rebate schedule",
        ),
    ],
)
def test_critical_proposals_require_dual_signoff_and_explicit_authorization_ref(
    domain: DomainKind,
    target_resource: str,
    summary: str,
    payload: dict[str, Any],
    rollback_plan: str,
) -> None:
    runner, _, key_provider = _make_runner()

    blocked_none = runner.execute(
        DomainExecutionRequest(
            run_id=f"run-crit-none-{domain.value}",
            domain=domain,
            target_resource=target_resource,
            summary=summary,
            payload=payload,
            rollback_plan=rollback_plan,
            builder_actor="critical-builder",
            verifier_actor="critical-verifier",
        )
    )
    assert blocked_none.release_decision.risk_tier == RiskTier.CRITICAL
    assert blocked_none.release_decision.can_release is False
    assert blocked_none.release_decision.outcome == ReleaseOutcome.RESTRICTED_AUTHORIZATION_REQUIRED
    assert blocked_none.applied is False
    assert blocked_none.apply_receipt is None
    _verify_dsse_and_decode(
        blocked_none,
        key_provider,
        expected_decision="rejected",
        expected_reviewer="critical-verifier",
    )

    blocked_single = runner.execute(
        DomainExecutionRequest(
            run_id=f"run-crit-single-{domain.value}",
            domain=domain,
            target_resource=target_resource,
            summary=summary,
            payload=payload,
            rollback_plan=rollback_plan,
            builder_actor="critical-builder",
            verifier_actor="critical-verifier",
            human_approvers=["guardian-1"],
            explicit_authorization_ref="CAB-2026-777",
        )
    )
    assert blocked_single.release_decision.can_release is False
    assert blocked_single.release_decision.outcome == ReleaseOutcome.RESTRICTED_AUTHORIZATION_REQUIRED
    assert blocked_single.applied is False
    assert blocked_single.apply_receipt is None

    approved = runner.execute(
        DomainExecutionRequest(
            run_id=f"run-crit-approved-{domain.value}",
            domain=domain,
            target_resource=target_resource,
            summary=summary,
            payload=payload,
            rollback_plan=rollback_plan,
            builder_actor="critical-builder",
            verifier_actor="critical-verifier",
            human_approvers=["guardian-1", "guardian-2"],
            explicit_authorization_ref="CAB-2026-777",
        )
    )
    assert approved.release_decision.can_release is True
    assert approved.release_decision.outcome == ReleaseOutcome.APPROVED_RESTRICTED
    assert approved.release_decision.assurance_achieved == AssuranceLevel.A4_RESTRICTED
    assert approved.release_decision.explicit_authorization_ref == "CAB-2026-777"
    assert approved.applied is True
    assert approved.apply_receipt is not None
    assert approved.apply_receipt["approved_decision_id"] == approved.release_decision.decision_id
    _verify_dsse_and_decode(
        approved,
        key_provider,
        expected_decision="approved",
        expected_reviewer="critical-verifier",
    )


def test_validation_failure_wildcard_cidr_and_missing_rollback_fail_closed() -> None:
    runner, mem_provider, key_provider = _make_runner()

    result = runner.execute(
        DomainExecutionRequest(
            run_id="run-fail-closed-validation",
            domain=DomainKind.INFRASTRUCTURE,
            target_resource="aws_security_group.public_db",
            summary="Open wildcard ingress without rollback",
            payload={"environment": "production", "cidr_blocks": ["0.0.0.0/0"]},
            rollback_plan="",
            builder_actor="tf-agent",
            verifier_actor="tf-verifier",
            human_approvers=["sre-guardian"],
        )
    )

    assert len(result.validation_errors) >= 2
    assert any("rollback_plan" in err for err in result.validation_errors)
    assert any("0.0.0.0/0" in err for err in result.validation_errors)
    assert result.proof_outcomes[0].passed is False
    assert result.release_decision.can_release is False
    assert result.release_decision.outcome == ReleaseOutcome.DENIED_POLICY
    assert result.applied is False
    assert result.apply_receipt is None

    _verify_dsse_and_decode(
        result,
        key_provider,
        expected_decision="rejected",
        expected_reviewer="tf-verifier",
    )

    facts = mem_provider.list_facts()
    assert len(facts) == 1
    assert facts[0].fact_type == FactType.VERIFICATION_FAILURE


def test_stale_evidence_and_drift_fail_closed() -> None:
    runner, _, key_provider = _make_runner()

    stale_result = runner.execute(
        DomainExecutionRequest(
            run_id="run-stale-evidence",
            domain=DomainKind.OPERATIONS,
            target_resource="service/checkout",
            summary="Restart dev checkout pod",
            payload={"environment": "dev"},
            rollback_plan="kubectl rollout restart deployment/checkout",
            evidence_fresh=False,
        )
    )
    assert stale_result.release_decision.can_release is False
    assert stale_result.release_decision.outcome == ReleaseOutcome.BLOCKED_STALE_EVIDENCE
    assert stale_result.applied is False
    assert stale_result.apply_receipt is None
    _verify_dsse_and_decode(
        stale_result,
        key_provider,
        expected_decision="rejected",
        expected_reviewer="domain-verifier-1",
    )

    drift_result = runner.execute(
        DomainExecutionRequest(
            run_id="run-drift-detected",
            domain=DomainKind.OPERATIONS,
            target_resource="service/checkout",
            summary="Restart dev checkout pod",
            payload={"environment": "dev"},
            rollback_plan="kubectl rollout restart deployment/checkout",
            drift_detected=True,
            stage=PolicyStage.RUNTIME,
        )
    )
    assert drift_result.release_decision.can_release is False
    assert drift_result.release_decision.outcome == ReleaseOutcome.BLOCKED_DRIFT
    assert drift_result.applied is False
    assert drift_result.apply_receipt is None


def test_actor_separation_violation_raises_permission_error() -> None:
    runner, _, _ = _make_runner()

    with pytest.raises(PermissionError, match="distinct from builder_actor"):
        runner.execute(
            DomainExecutionRequest(
                run_id="run-sod-violation",
                domain=DomainKind.INFRASTRUCTURE,
                target_resource="aws_s3_bucket.logs",
                summary="Enable encryption",
                payload={"environment": "staging"},
                rollback_plan="Disable encryption rule",
                builder_actor="same-agent",
                verifier_actor="  same-agent  ",
            )
        )

    with pytest.raises(PermissionError, match="non-empty"):
        runner.execute(
            DomainExecutionRequest(
                run_id="run-empty-actor",
                domain=DomainKind.INFRASTRUCTURE,
                target_resource="aws_s3_bucket.logs",
                summary="Enable encryption",
                payload={"environment": "staging"},
                rollback_plan="Disable encryption rule",
                builder_actor="   ",
                verifier_actor="verifier-1",
            )
        )


def test_redacts_pii_and_persists_to_store_hooks(tmp_path: Path) -> None:
    class _DuckStore:
        def __init__(self) -> None:
            self.decisions: list[ReleaseDecision] = []
            self.proposals: list[Any] = []

        def save_release_decision(self, decision: ReleaseDecision) -> ReleaseDecision:
            self.decisions.append(decision)
            return decision

        def save_domain_proposal_record(self, record: Any) -> Any:
            self.proposals.append(record)
            return record

    duck_store = _DuckStore()
    runner, mem_provider, _ = _make_runner(store=duck_store)

    result = runner.execute(
        DomainExecutionRequest(
            run_id="run-store-redact",
            domain=DomainKind.BUSINESS,
            target_resource="crm/account-tier",
            summary="Update tier requested by alice@example.com",
            payload={"financial_impact_usd": 750.0},
            rollback_plan="Restore prior CRM tier",
        )
    )

    assert len(duck_store.decisions) == 1
    assert duck_store.decisions[0].decision_id == result.release_decision.decision_id
    assert len(duck_store.proposals) == 1
    saved_prop = duck_store.proposals[0]
    prop_id = saved_prop["proposal_id"] if isinstance(saved_prop, dict) else saved_prop.proposal_id
    prop_applied = saved_prop["applied"] if isinstance(saved_prop, dict) else saved_prop.applied
    assert prop_id == result.proposal.proposal_id
    assert prop_applied is True

    facts = mem_provider.list_facts()
    assert len(facts) == 1
    assert "alice@example.com" not in facts[0].summary
    assert "[REDACTED]" in facts[0].summary

    # Also verify compatibility with concrete ServerStore (Schema v13)
    sqlite_store = ServerStore(tmp_path / "vad.db")
    sqlite_store.migrate()
    runner_with_sqlite, _, _ = _make_runner(store=sqlite_store)
    sqlite_res = runner_with_sqlite.execute(
        DomainExecutionRequest(
            run_id="run-sqlite-store",
            domain=DomainKind.OPERATIONS,
            target_resource="svc/billing",
            summary="Scale dev workers",
            payload={"environment": "dev"},
            rollback_plan="Scale down dev workers",
        )
    )
    loaded_decisions = sqlite_store.list_release_decisions(run_id="run-sqlite-store")
    assert len(loaded_decisions) == 1
    assert loaded_decisions[0].decision_id == sqlite_res.release_decision.decision_id
    loaded_proposal = sqlite_store.load_domain_proposal_record(sqlite_res.proposal.proposal_id)
    assert loaded_proposal.run_id == "run-sqlite-store"
    assert loaded_proposal.applied is True
    assert loaded_proposal.decision_id == sqlite_res.release_decision.decision_id


def test_default_constructor_works_with_null_memory_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    runner = DomainPackExecutionRunner()
    result = runner.execute(
        DomainExecutionRequest(
            run_id="run-default-runner",
            domain=DomainKind.OPERATIONS,
            target_resource="svc/health",
            summary="Dev health probe check",
            payload={"environment": "dev"},
            rollback_plan="Revert probe config",
        )
    )
    assert result.applied is True
    assert result.published_fact_id is not None
