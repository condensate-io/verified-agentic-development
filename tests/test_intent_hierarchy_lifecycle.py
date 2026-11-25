"""Unit tests for Hierarchical Intent 3-tier governance, lifecycle transitions, and JSON Schema."""

import json
from pathlib import Path

import jsonschema
import pytest

from vad.contracts.intent_hierarchy import (
    ChildEIPRef,
    DecompositionStatus,
    EngineeringObjective,
    IntentLevel,
    Programme,
    StrategicObjective,
    approve_decomposition,
    compute_blocked_dependents,
    record_child_eip_verification,
    submit_decomposition_for_approval,
    validate_hierarchy_chain,
)
from vad.contracts.models import AssuranceLevel, RiskTier


def _make_child(
    name: str,
    *,
    risk_tier: RiskTier = RiskTier.HIGH,
    assurance_level: AssuranceLevel = AssuranceLevel.A3_ATTESTED,
    depends_on: list[str] | None = None,
    verified: bool = False,
    evidence_fresh: bool = False,
    evidence_digest: str | None = None,
    cost_usd: float = 10.0,
) -> ChildEIPRef:
    return ChildEIPRef(
        eip_name=name,
        eip_digest=f"sha256:{name}-digest",
        risk_tier=risk_tier,
        assurance_level=assurance_level,
        depends_on=depends_on or [],
        verified=verified,
        evidence_fresh=evidence_fresh,
        evidence_digest=evidence_digest,
        cost_usd=cost_usd,
    )


def _make_three_tier_chain() -> tuple[StrategicObjective, Programme, EngineeringObjective]:
    strategic = StrategicObjective(
        node_id="so-platform-security",
        title="Platform Security Hardening",
        description="Enforce attested control-plane security across services.",
        risk_floor=RiskTier.MEDIUM,
        assurance_floor=AssuranceLevel.A2_INDEPENDENT,
        inherited_invariants=["verifier_not_builder", "no_plaintext_secrets"],
        max_total_cost_usd=500.0,
        decomposition_status=DecompositionStatus.APPROVED,
        approved_by="principal-architect",
    )
    programme = Programme(
        node_id="prog-zero-trust",
        title="Zero-Trust Control Plane",
        description="Isolate worker credentials and enforce mTLS.",
        parent_id=strategic.node_id,
        risk_floor=RiskTier.HIGH,
        assurance_floor=AssuranceLevel.A3_ATTESTED,
        inherited_invariants=[
            "verifier_not_builder",
            "no_plaintext_secrets",
            "mtls_required",
        ],
        max_total_cost_usd=200.0,
        decomposition_status=DecompositionStatus.APPROVED,
        approved_by="programme-lead",
    )
    engineering = EngineeringObjective(
        node_id="eo-worker-auth",
        title="Worker Credential Isolation",
        description="Roll out scoped host tokens and DSSE verification.",
        parent_id=programme.node_id,
        risk_floor=RiskTier.HIGH,
        assurance_floor=AssuranceLevel.A3_ATTESTED,
        inherited_invariants=[
            "verifier_not_builder",
            "no_plaintext_secrets",
            "mtls_required",
            "scoped_tokens",
        ],
        max_total_cost_usd=100.0,
        child_eips=[
            _make_child("eip-token-schema", cost_usd=25.0),
            _make_child(
                "eip-token-enforcer",
                risk_tier=RiskTier.CRITICAL,
                assurance_level=AssuranceLevel.A4_RESTRICTED,
                depends_on=["eip-token-schema"],
                cost_usd=50.0,
            ),
        ],
    )
    return strategic, programme, engineering


def test_validate_hierarchy_chain_valid_three_tier_and_direct_two_tier() -> None:
    strategic, programme, engineering = _make_three_tier_chain()
    assert validate_hierarchy_chain(strategic, programme, engineering) == []

    # Direct StrategicObjective -> EngineeringObjective (no intermediate Programme)
    assert validate_hierarchy_chain(strategic, None, engineering) == []

    # Programme -> EngineeringObjective (no StrategicObjective supplied)
    assert validate_hierarchy_chain(None, programme, engineering) == []

    # Standalone EngineeringObjective
    assert validate_hierarchy_chain(None, None, engineering) == []


def test_validate_hierarchy_chain_detects_level_and_parent_id_mismatches() -> None:
    strategic, programme, engineering = _make_three_tier_chain()

    bad_strategic = strategic.model_copy(update={"level": IntentLevel.PROGRAMME})
    bad_programme = programme.model_copy(
        update={"level": IntentLevel.ENGINEERING_OBJECTIVE, "parent_id": "so-wrong"}
    )
    bad_engineering = engineering.model_copy(
        update={"level": IntentLevel.EIP, "parent_id": "prog-wrong"}
    )

    violations = validate_hierarchy_chain(bad_strategic, bad_programme, bad_engineering)
    assert any("StrategicObjective" in v and "invalid level" in v for v in violations)
    assert any("Programme" in v and "invalid level" in v for v in violations)
    assert any("EngineeringObjective" in v and "invalid level" in v for v in violations)
    assert any("Programme" in v and "parent_id" in v for v in violations)
    assert any("EngineeringObjective" in v and "parent_id" in v for v in violations)


def test_validate_hierarchy_chain_rejects_floor_downgrades_at_programme_and_objective() -> None:
    strategic, programme, engineering = _make_three_tier_chain()

    # Programme downgrades below StrategicObjective
    downgraded_prog = programme.model_copy(
        update={
            "risk_floor": RiskTier.LOW,
            "assurance_floor": AssuranceLevel.A1_BASIC,
        }
    )
    prog_violations = validate_hierarchy_chain(strategic, downgraded_prog, engineering)
    assert any("Programme" in v and "risk_floor" in v for v in prog_violations)
    assert any("Programme" in v and "assurance_floor" in v for v in prog_violations)

    # EngineeringObjective downgrades below Programme
    downgraded_eng = engineering.model_copy(
        update={
            "risk_floor": RiskTier.MEDIUM,
            "assurance_floor": AssuranceLevel.A2_INDEPENDENT,
        }
    )
    eng_violations = validate_hierarchy_chain(strategic, programme, downgraded_eng)
    assert any("EngineeringObjective" in v and "risk_floor" in v for v in eng_violations)
    assert any("EngineeringObjective" in v and "assurance_floor" in v for v in eng_violations)

    # EngineeringObjective downgrades directly below StrategicObjective (without Programme)
    direct_downgraded_eng = engineering.model_copy(
        update={
            "risk_floor": RiskTier.LOW,
            "assurance_floor": AssuranceLevel.A1_BASIC,
        }
    )
    direct_violations = validate_hierarchy_chain(strategic, None, direct_downgraded_eng)
    assert any("EngineeringObjective" in v and "risk_floor" in v for v in direct_violations)
    assert any("EngineeringObjective" in v and "assurance_floor" in v for v in direct_violations)


def test_validate_hierarchy_chain_rejects_dropped_invariants_and_budget_breach() -> None:
    strategic, programme, engineering = _make_three_tier_chain()

    bad_prog = programme.model_copy(
        update={
            "inherited_invariants": ["verifier_not_builder"],
            "max_total_cost_usd": 600.0,
        }
    )
    bad_eng = engineering.model_copy(
        update={
            "inherited_invariants": ["verifier_not_builder", "no_plaintext_secrets"],
            "max_total_cost_usd": None,
        }
    )

    violations = validate_hierarchy_chain(strategic, bad_prog, bad_eng)
    assert any("Programme" in v and "no_plaintext_secrets" in v for v in violations)
    assert any("Programme" in v and "max_total_cost_usd" in v for v in violations)
    assert any("EngineeringObjective" in v and "must specify max_total_cost_usd" in v for v in violations)

    # Engineering budget exceeding programme budget, and direct strategic check without programme
    over_budget_eng = engineering.model_copy(update={"max_total_cost_usd": 250.0})
    over_violations = validate_hierarchy_chain(strategic, programme, over_budget_eng)
    assert any("EngineeringObjective" in v and "$250.00" in v for v in over_violations)

    no_budget_eng = engineering.model_copy(update={"max_total_cost_usd": None})
    direct_budget_violations = validate_hierarchy_chain(strategic, None, no_budget_eng)
    assert any("EngineeringObjective" in v and "max_total_cost_usd" in v for v in direct_budget_violations)


def test_validate_hierarchy_chain_rejects_unknown_depends_on_target() -> None:
    strategic, programme, engineering = _make_three_tier_chain()
    bad_eng = engineering.model_copy(
        update={
            "child_eips": [
                _make_child("eip-token-schema", cost_usd=25.0),
                _make_child(
                    "eip-token-enforcer",
                    depends_on=["eip-token-schema", "eip-nonexistent"],
                    cost_usd=50.0,
                ),
            ]
        }
    )

    violations = validate_hierarchy_chain(strategic, programme, bad_eng)
    assert any("eip-nonexistent" in v and "unknown target" in v for v in violations)


def test_submit_decomposition_for_approval_transitions_and_validates() -> None:
    _, _, engineering = _make_three_tier_chain()
    draft_with_stale_approver = engineering.model_copy(
        update={"approved_by": "stale-approver"}
    )

    submitted = submit_decomposition_for_approval(draft_with_stale_approver)
    assert submitted.decomposition_status == DecompositionStatus.AWAITING_APPROVAL
    assert submitted.approved_by is None
    assert draft_with_stale_approver.decomposition_status == DecompositionStatus.DRAFT

    # Empty children rejected
    with pytest.raises(ValueError, match="no child EIPs"):
        submit_decomposition_for_approval(engineering.model_copy(update={"child_eips": []}))

    # Floor violation rejected
    bad_floor = engineering.model_copy(
        update={
            "child_eips": [
                _make_child("eip-bad", risk_tier=RiskTier.LOW, cost_usd=10.0)
            ]
        }
    )
    with pytest.raises(ValueError, match="validation violations"):
        submit_decomposition_for_approval(bad_floor)

    # Disallowed starting status rejected
    already_approved = engineering.model_copy(
        update={"decomposition_status": DecompositionStatus.APPROVED}
    )
    with pytest.raises(ValueError, match="Cannot submit decomposition"):
        submit_decomposition_for_approval(already_approved)


def test_approve_decomposition_role_and_floor_checks() -> None:
    _, _, engineering = _make_three_tier_chain()
    awaiting = submit_decomposition_for_approval(engineering)

    for role in ("release_guardian", "OPERATOR", "Principal_Architect", "programme_lead"):
        approved = approve_decomposition(
            awaiting,
            approver="  alice.guardian  ",
            approver_role=role,
        )
        assert approved.decomposition_status == DecompositionStatus.APPROVED
        assert approved.approved_by == "alice.guardian"

    # Unauthorized roles and blank approver rejected with PermissionError
    with pytest.raises(PermissionError):
        approve_decomposition(awaiting, approver="bob", approver_role="builder")
    with pytest.raises(PermissionError):
        approve_decomposition(awaiting, approver="   ", approver_role="release_guardian")

    # Empty child EIPs or floor violations rejected with ValueError
    with pytest.raises(ValueError, match="no child EIPs"):
        approve_decomposition(
            awaiting.model_copy(update={"child_eips": []}),
            approver="alice",
        )
    with pytest.raises(ValueError, match="validation violations"):
        approve_decomposition(
            awaiting.model_copy(
                update={
                    "child_eips": [
                        _make_child(
                            "eip-low-assurance",
                            assurance_level=AssuranceLevel.A1_BASIC,
                        )
                    ]
                }
            ),
            approver="alice",
        )


def test_record_child_eip_verification_gates_and_transitions() -> None:
    _, _, engineering = _make_three_tier_chain()

    # Reject unapproved statuses
    for blocked_status in (
        DecompositionStatus.DRAFT,
        DecompositionStatus.AWAITING_APPROVAL,
        DecompositionStatus.FAILED,
        DecompositionStatus.CANCELLED,
    ):
        obj = engineering.model_copy(update={"decomposition_status": blocked_status})
        with pytest.raises(PermissionError):
            record_child_eip_verification(
                obj,
                eip_name="eip-token-schema",
                verified=True,
                evidence_fresh=True,
                evidence_digest="sha256:ev-1",
            )

    approved = approve_decomposition(
        submit_decomposition_for_approval(engineering),
        approver="guardian-1",
    )

    with pytest.raises(KeyError):
        record_child_eip_verification(
            approved,
            eip_name="eip-missing",
            verified=True,
            evidence_fresh=True,
        )

    executing = record_child_eip_verification(
        approved,
        eip_name="eip-token-schema",
        verified=True,
        evidence_fresh=True,
        evidence_digest="sha256:ev-schema",
        cost_usd=27.5,
    )
    assert executing.decomposition_status == DecompositionStatus.EXECUTING
    assert approved.decomposition_status == DecompositionStatus.APPROVED
    assert approved.child_eips[0].verified is False

    updated_first = executing.child_eips[0]
    assert updated_first.verified is True
    assert updated_first.evidence_fresh is True
    assert updated_first.evidence_digest == "sha256:ev-schema"
    assert updated_first.cost_usd == 27.5
    assert executing.child_eips[1].verified is False


def test_compute_blocked_dependents_transitive_on_failed_or_stale_leaf() -> None:
    assert compute_blocked_dependents([]) == {}

    all_healthy = [
        _make_child("eip-a", verified=True, evidence_fresh=True),
        _make_child("eip-b", depends_on=["eip-a"], verified=True, evidence_fresh=True),
    ]
    assert compute_blocked_dependents(all_healthy) == {}

    # eip-a fails verification, eip-stale is verified but stale; eip-independent is unaffected
    children = [
        _make_child("eip-a", verified=False, evidence_fresh=True),
        _make_child("eip-b", depends_on=["eip-a"], verified=True, evidence_fresh=True),
        _make_child("eip-c", depends_on=["eip-b"], verified=True, evidence_fresh=True),
        _make_child("eip-stale", verified=True, evidence_fresh=False),
        _make_child(
            "eip-d",
            depends_on=["eip-b", "eip-stale"],
            verified=True,
            evidence_fresh=True,
        ),
        _make_child("eip-independent", verified=True, evidence_fresh=True),
    ]

    blocked = compute_blocked_dependents(children)
    assert blocked == {
        "eip-b": ["eip-a"],
        "eip-c": ["eip-a"],
        "eip-d": ["eip-a", "eip-stale"],
    }
    assert "eip-a" not in blocked
    assert "eip-stale" not in blocked
    assert "eip-independent" not in blocked


def test_intent_hierarchy_json_schema_structure_and_validation() -> None:
    schema_path = (
        Path(__file__).resolve().parents[1]
        / "schemas"
        / "intent_hierarchy.schema.json"
    )
    schema = json.loads(schema_path.read_text(encoding="utf-8"))

    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    jsonschema.Draft202012Validator.check_schema(schema)

    defs = schema["$defs"]
    for key in (
        "StrategicObjective",
        "Programme",
        "EngineeringObjective",
        "ChildEIPRef",
        "IntentLevel",
        "DecompositionStatus",
        "RiskTier",
        "AssuranceLevel",
    ):
        assert key in defs

    validator = jsonschema.Draft202012Validator(schema)
    strategic, programme, engineering = _make_three_tier_chain()

    for model in (strategic, programme, engineering, engineering.child_eips[0]):
        validator.validate(model.model_dump(mode="json"))

    # Mismatched level or invalid RiskTier must fail schema validation
    invalid_level_payload = strategic.model_dump(mode="json")
    invalid_level_payload["level"] = "programme"
    invalid_level_payload["unknown_field"] = True
    with pytest.raises(jsonschema.ValidationError):
        validator.validate(invalid_level_payload)

    invalid_risk_payload = engineering.child_eips[0].model_dump(mode="json")
    invalid_risk_payload["risk_tier"] = "extreme"
    with pytest.raises(jsonschema.ValidationError):
        validator.validate(invalid_risk_payload)
