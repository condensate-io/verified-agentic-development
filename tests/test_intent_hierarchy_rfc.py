"""Tests for Hierarchical Intent Model RFC contracts and rollup governance."""

from pathlib import Path
import pytest

from vad.contracts.intent_hierarchy import (
    ChildEIPRef,
    DecompositionStatus,
    EngineeringObjective,
    IntentLevel,
    Programme,
    StrategicObjective,
    compute_execution_tranches,
    evaluate_objective_rollup,
    validate_child_floor_inheritance,
)
from vad.contracts.models import AssuranceLevel, RiskTier


def _make_child(
    name: str,
    *,
    risk_tier: RiskTier = RiskTier.HIGH,
    assurance_level: AssuranceLevel = AssuranceLevel.A3_ATTESTED,
    depends_on: list[str] | None = None,
    verified: bool = True,
    evidence_fresh: bool = True,
    evidence_digest: str | None = "sha256:ev123",
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


def test_full_hierarchy_chain_and_floor_escalation() -> None:
    strategic = StrategicObjective(
        node_id="so-zero-trust",
        title="Zero-Trust Control Plane",
        description="Enforce verified identity and attestation across services.",
        risk_floor=RiskTier.HIGH,
        assurance_floor=AssuranceLevel.A3_ATTESTED,
        inherited_invariants=["no_plaintext_secrets", "verifier_not_builder"],
        max_total_cost_usd=500.0,
        decomposition_status=DecompositionStatus.APPROVED,
        approved_by="principal-architect",
    )
    programme = Programme(
        node_id="prog-auth-modernization",
        title="Auth & Token Modernization",
        description="Migrate gateway and worker auth to Ed25519 tokens.",
        parent_id=strategic.node_id,
        risk_floor=strategic.risk_floor,
        assurance_floor=strategic.assurance_floor,
        inherited_invariants=list(strategic.inherited_invariants),
        max_total_cost_usd=200.0,
        decomposition_status=DecompositionStatus.APPROVED,
        approved_by="programme-lead",
    )
    objective = EngineeringObjective(
        node_id="eo-gateway-mtls",
        title="Gateway & Worker Session Hardening",
        description="Roll out scoped tokens and DSSE verification.",
        parent_id=programme.node_id,
        risk_floor=programme.risk_floor,
        assurance_floor=programme.assurance_floor,
        inherited_invariants=list(programme.inherited_invariants),
        max_total_cost_usd=100.0,
        decomposition_status=DecompositionStatus.APPROVED,
        approved_by="security-owner",
        require_coordinated_release=True,
        cross_eip_proof_passed=True,
        child_eips=[
            _make_child(
                "eip-schema-keys",
                risk_tier=RiskTier.HIGH,
                assurance_level=AssuranceLevel.A3_ATTESTED,
                cost_usd=30.0,
            ),
            _make_child(
                "eip-gateway-enforcer",
                risk_tier=RiskTier.CRITICAL,
                assurance_level=AssuranceLevel.A4_RESTRICTED,
                depends_on=["eip-schema-keys"],
                cost_usd=45.0,
            ),
        ],
    )

    assert strategic.level == IntentLevel.STRATEGIC_OBJECTIVE
    assert programme.level == IntentLevel.PROGRAMME
    assert programme.parent_id == strategic.node_id
    assert objective.level == IntentLevel.ENGINEERING_OBJECTIVE
    assert objective.parent_id == programme.node_id
    assert validate_child_floor_inheritance(objective) == []
    assert evaluate_objective_rollup(objective) == (True, [])


def test_non_downgradable_risk_and_assurance_floor_rejection() -> None:
    objective = EngineeringObjective(
        node_id="eo-floor-check",
        title="High-Risk Migration",
        description="Requires HIGH risk and A3_ATTESTED assurance floor.",
        risk_floor=RiskTier.HIGH,
        assurance_floor=AssuranceLevel.A3_ATTESTED,
        decomposition_status=DecompositionStatus.APPROVED,
        cross_eip_proof_passed=True,
        child_eips=[
            _make_child(
                "eip-downgraded-risk",
                risk_tier=RiskTier.LOW,
                assurance_level=AssuranceLevel.A3_ATTESTED,
            ),
            _make_child(
                "eip-downgraded-assurance",
                risk_tier=RiskTier.HIGH,
                assurance_level=AssuranceLevel.A2_INDEPENDENT,
            ),
        ],
    )

    violations = validate_child_floor_inheritance(objective)
    assert len(violations) == 2
    assert any("eip-downgraded-risk" in v and "risk_floor" in v for v in violations)
    assert any("eip-downgraded-assurance" in v and "assurance_floor" in v for v in violations)

    ok, reasons = evaluate_objective_rollup(objective)
    assert ok is False
    assert len(reasons) >= 2


def test_budget_ceiling_rollup_violation() -> None:
    objective = EngineeringObjective(
        node_id="eo-budget-cap",
        title="Budget-Bounded Objective",
        description="Prevent budget bypass via EIP fragmentation.",
        risk_floor=RiskTier.LOW,
        assurance_floor=AssuranceLevel.A1_BASIC,
        max_total_cost_usd=25.0,
        decomposition_status=DecompositionStatus.APPROVED,
        cross_eip_proof_passed=True,
        child_eips=[
            _make_child("eip-1", risk_tier=RiskTier.LOW, assurance_level=AssuranceLevel.A1_BASIC, cost_usd=10.0),
            _make_child("eip-2", risk_tier=RiskTier.LOW, assurance_level=AssuranceLevel.A1_BASIC, cost_usd=10.0),
            _make_child("eip-3", risk_tier=RiskTier.LOW, assurance_level=AssuranceLevel.A1_BASIC, cost_usd=6.0),
        ],
    )

    violations = validate_child_floor_inheritance(objective)
    assert len(violations) == 1
    assert "max_total_cost_usd" in violations[0]

    ok, reasons = evaluate_objective_rollup(objective)
    assert ok is False
    assert any("max_total_cost_usd" in r for r in reasons)


def test_cycle_detection_in_depends_on() -> None:
    cyclic_children = [
        _make_child("eip-a", depends_on=["eip-c"]),
        _make_child("eip-b", depends_on=["eip-a"]),
        _make_child("eip-c", depends_on=["eip-b"]),
    ]
    with pytest.raises(ValueError, match="Cyclic dependency"):
        compute_execution_tranches(cyclic_children)

    self_loop = [_make_child("eip-self", depends_on=["eip-self"])]
    with pytest.raises(ValueError, match="Cyclic dependency"):
        compute_execution_tranches(self_loop)

    objective = EngineeringObjective(
        node_id="eo-cyclic",
        title="Cyclic DAG",
        description="Should be rejected by floor/DAG validation.",
        risk_floor=RiskTier.HIGH,
        assurance_floor=AssuranceLevel.A3_ATTESTED,
        decomposition_status=DecompositionStatus.APPROVED,
        cross_eip_proof_passed=True,
        child_eips=cyclic_children,
    )
    violations = validate_child_floor_inheritance(objective)
    assert any("Cyclic dependency" in v for v in violations)


def test_topological_parallel_tranche_grouping() -> None:
    assert compute_execution_tranches([]) == []

    children = [
        _make_child("eip-schema"),
        _make_child("eip-config"),
        _make_child("eip-api", depends_on=["eip-schema", "eip-config"]),
        _make_child("eip-worker", depends_on=["eip-schema"]),
        _make_child("eip-e2e", depends_on=["eip-api", "eip-worker"]),
    ]

    tranches = compute_execution_tranches(children)
    assert tranches == [
        ["eip-schema", "eip-config"],
        ["eip-api", "eip-worker"],
        ["eip-e2e"],
    ]


def test_rollup_fails_on_unapproved_stale_unverified_or_failed_cross_eip() -> None:
    base_children = [
        _make_child("eip-1", verified=True, evidence_fresh=True),
        _make_child("eip-2", depends_on=["eip-1"], verified=True, evidence_fresh=False),
    ]

    # Draft decomposition + stale child + missing cross-EIP proof
    draft_obj = EngineeringObjective(
        node_id="eo-fail-cases",
        title="Rollup Failure Modes",
        description="Tests all rollup rejection gates.",
        risk_floor=RiskTier.HIGH,
        assurance_floor=AssuranceLevel.A3_ATTESTED,
        decomposition_status=DecompositionStatus.DRAFT,
        cross_eip_proof_passed=False,
        child_eips=base_children,
    )
    ok, reasons = evaluate_objective_rollup(draft_obj)
    assert ok is False
    assert any("decomposition_status" in r for r in reasons)
    assert any("fresh evidence" in r for r in reasons)
    assert any("Cross-EIP" in r for r in reasons)

    # Approved, all fresh & verified, but cross-EIP proof failed
    missing_cross = draft_obj.model_copy(
        update={
            "decomposition_status": DecompositionStatus.EXECUTING,
            "child_eips": [
                _make_child("eip-1", verified=True, evidence_fresh=True),
                _make_child("eip-2", depends_on=["eip-1"], verified=True, evidence_fresh=True),
            ],
            "cross_eip_proof_passed": False,
        }
    )
    ok, reasons = evaluate_objective_rollup(missing_cross)
    assert ok is False
    assert reasons == ["Cross-EIP integration proof obligations have not passed"]

    # Approved, cross-EIP passed, but one child unverified
    unverified_child = missing_cross.model_copy(
        update={
            "cross_eip_proof_passed": True,
            "child_eips": [
                _make_child("eip-1", verified=True, evidence_fresh=True),
                _make_child("eip-2", depends_on=["eip-1"], verified=False, evidence_fresh=True),
            ],
        }
    )
    ok, reasons = evaluate_objective_rollup(unverified_child)
    assert ok is False
    assert reasons == ["Child EIP 'eip-2' is not verified"]

    # Empty children cannot trivially pass rollup
    empty_obj = missing_cross.model_copy(
        update={"child_eips": [], "cross_eip_proof_passed": True}
    )
    ok, reasons = evaluate_objective_rollup(empty_obj)
    assert ok is False
    assert any("no child EIPs" in r for r in reasons)


def test_rollup_succeeds_when_approved_fresh_verified_and_cross_proof_passed() -> None:
    for status in (
        DecompositionStatus.APPROVED,
        DecompositionStatus.EXECUTING,
        DecompositionStatus.VERIFIED,
    ):
        objective = EngineeringObjective(
            node_id="eo-pass",
            title="Verified Multi-EIP Objective",
            description="All governance and verification conditions satisfied.",
            risk_floor=RiskTier.MEDIUM,
            assurance_floor=AssuranceLevel.A2_INDEPENDENT,
            max_total_cost_usd=50.0,
            decomposition_status=status,
            approved_by="release-guardian",
            cross_eip_proof_passed=True,
            child_eips=[
                _make_child(
                    "eip-core",
                    risk_tier=RiskTier.MEDIUM,
                    assurance_level=AssuranceLevel.A2_INDEPENDENT,
                    cost_usd=20.0,
                ),
                _make_child(
                    "eip-edge",
                    risk_tier=RiskTier.HIGH,
                    assurance_level=AssuranceLevel.A3_ATTESTED,
                    depends_on=["eip-core"],
                    cost_usd=30.0,
                ),
            ],
        )
        ok, reasons = evaluate_objective_rollup(objective)
        assert ok is True
        assert reasons == []


def test_rfc_doc_covers_options_and_governance_rules() -> None:
    rfc_path = (
        Path(__file__).resolve().parents[1]
        / "docs"
        / "architecture"
        / "rfc-hierarchical-intent.md"
    )
    text = rfc_path.read_text(encoding="utf-8")
    for marker in [
        "StrategicObjective",
        "Programme",
        "EngineeringObjective",
        "Option A",
        "Option B",
        "Option C",
        "Decomposition Governance",
        "Non-Downgradable Floor Inheritance",
        "Rollup Verification Semantics",
        "Aggregate Budget & Blast-Radius Ceilings",
        "Partial Failure & Coordinated Release",
    ]:
        assert marker in text
