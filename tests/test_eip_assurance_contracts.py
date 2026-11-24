import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from vad.contracts.models import (
    AssuranceLevel,
    AutonomyTier,
    Constraints,
    EIP,
    FreshnessPolicy,
    Goal,
    Invariants,
    MemoryRequirement,
    ModelBudget,
    ProofKind,
    ProofObligation,
    ReleaseRequirements,
    RiskTier,
    TelemetryRequirements,
    ToolPermissions,
    default_assurance_for_risk,
    default_freshness_for_risk,
)
from vad.contracts.normalize import normalize_eip


SCHEMA_PATH = Path(__file__).resolve().parents[1] / "schemas" / "eip.schema.json"


def _minimal_eip_payload(**overrides):
    payload = {
        "version": "1.0.0",
        "name": "legacy-compatible-eip",
        "goal": {"description": "Preserve legacy EIP shape", "success_criteria": ["tests pass"]},
        "non_goals": ["unrequested refactor"],
        "risk_tier": "low",
        "autonomy_tier": "assisted",
        "scope_boundaries": ["vad/contracts"],
        "invariants": {"security": [], "performance": [], "functional": []},
        "constraints": {"security": [], "privacy": [], "operational": []},
        "proof_obligations": [
            {"id": "po-1", "kind": "unit", "description": "unit proof"},
        ],
        "tool_permissions": {"allowed": ["pytest"], "denied": ["network"]},
        "memory_requirements": [{"scope": "project", "purpose": "tests"}],
        "model_budget": {"max_tokens": 1000, "max_cost": 1.0, "max_loop_depth": 3},
        "release_requirements": {"required": False, "gates": []},
        "telemetry_requirements": {"required": False, "signals": []},
    }
    payload.update(overrides)
    return payload


def test_risk_tier_critical_and_assurance_levels():
    assert RiskTier.CRITICAL.value == "critical"
    assert [level.value for level in AssuranceLevel] == [
        "A1_BASIC",
        "A2_INDEPENDENT",
        "A3_ATTESTED",
        "A4_RESTRICTED",
    ]


def test_freshness_policy_defaults_and_bounds():
    policy = FreshnessPolicy()
    assert policy.max_evidence_age_seconds == 14400
    assert policy.require_head_commit_match is True
    assert policy.require_tree_hash_match is True
    assert policy.require_env_digest_match is False
    assert policy.invalidate_on_dependency_change is True

    with pytest.raises(ValidationError):
        FreshnessPolicy(max_evidence_age_seconds=0)


@pytest.mark.parametrize(
    ("risk_tier", "expected_assurance"),
    [
        (RiskTier.LOW, AssuranceLevel.A1_BASIC),
        (RiskTier.MEDIUM, AssuranceLevel.A2_INDEPENDENT),
        (RiskTier.HIGH, AssuranceLevel.A3_ATTESTED),
        (RiskTier.CRITICAL, AssuranceLevel.A4_RESTRICTED),
    ],
)
def test_default_assurance_for_risk(risk_tier: RiskTier, expected_assurance: AssuranceLevel):
    assert default_assurance_for_risk(risk_tier) == expected_assurance


@pytest.mark.parametrize(
    ("risk_tier", "expected_age", "expected_tree", "expected_env"),
    [
        (RiskTier.LOW, 86400, False, False),
        (RiskTier.MEDIUM, 14400, True, False),
        (RiskTier.HIGH, 3600, True, True),
        (RiskTier.CRITICAL, 900, True, True),
    ],
)
def test_default_freshness_for_risk(
    risk_tier: RiskTier,
    expected_age: int,
    expected_tree: bool,
    expected_env: bool,
):
    policy = default_freshness_for_risk(risk_tier)
    assert policy.max_evidence_age_seconds == expected_age
    assert policy.require_head_commit_match is True
    assert policy.require_tree_hash_match is expected_tree
    assert policy.require_env_digest_match is expected_env
    assert policy.invalidate_on_dependency_change is True


def test_backward_compatibility_of_minimal_existing_eip_payload():
    payload = _minimal_eip_payload()
    eip = EIP(**payload)

    assert eip.assurance_level is None
    assert eip.freshness_policy is None
    assert eip.proof_obligations[0].min_assurance_level is None
    assert eip.proof_obligations[0].independent_execution_required is False

    assert eip.effective_assurance_level() == AssuranceLevel.A1_BASIC
    assert eip.effective_freshness_policy() == default_freshness_for_risk(RiskTier.LOW)


def test_effective_assurance_and_freshness_respect_explicit_overrides():
    custom_policy = FreshnessPolicy(
        max_evidence_age_seconds=1200,
        require_head_commit_match=True,
        require_tree_hash_match=True,
        require_env_digest_match=True,
        invalidate_on_dependency_change=False,
    )
    eip = EIP(
        **_minimal_eip_payload(
            risk_tier=RiskTier.MEDIUM,
            assurance_level=AssuranceLevel.A3_ATTESTED,
            freshness_policy=custom_policy,
        )
    )

    assert eip.effective_assurance_level() == AssuranceLevel.A3_ATTESTED
    assert eip.effective_freshness_policy() == custom_policy


@pytest.mark.parametrize(
    "downgraded_level",
    [
        AssuranceLevel.A1_BASIC,
        AssuranceLevel.A2_INDEPENDENT,
        AssuranceLevel.A3_ATTESTED,
    ],
)
def test_critical_eip_rejects_downgraded_assurance(downgraded_level: AssuranceLevel):
    with pytest.raises(ValidationError, match="critical"):
        EIP(
            **_minimal_eip_payload(
                risk_tier=RiskTier.CRITICAL,
                assurance_level=downgraded_level,
            )
        )


def test_critical_eip_accepts_default_or_a4_restricted():
    implicit = EIP(**_minimal_eip_payload(risk_tier=RiskTier.CRITICAL))
    assert implicit.effective_assurance_level() == AssuranceLevel.A4_RESTRICTED
    assert implicit.effective_freshness_policy() == default_freshness_for_risk(RiskTier.CRITICAL)

    explicit = EIP(
        **_minimal_eip_payload(
            risk_tier=RiskTier.CRITICAL,
            assurance_level=AssuranceLevel.A4_RESTRICTED,
        )
    )
    assert explicit.effective_assurance_level() == AssuranceLevel.A4_RESTRICTED


def test_deterministic_normalization_with_assurance_and_freshness():
    po_a = ProofObligation(
        id="po-1",
        kind=ProofKind.SECURITY,
        description="same desc",
        min_assurance_level=AssuranceLevel.A3_ATTESTED,
        independent_execution_required=True,
    )
    po_b = ProofObligation(
        id="po-1",
        kind=ProofKind.SECURITY,
        description="same desc",
        min_assurance_level=AssuranceLevel.A2_INDEPENDENT,
        independent_execution_required=False,
    )
    po_c = ProofObligation(
        id="po-1",
        kind=ProofKind.SECURITY,
        description="same desc",
        min_assurance_level=None,
        independent_execution_required=True,
    )
    po_d = ProofObligation(
        id="po-1",
        kind=ProofKind.SECURITY,
        description="same desc",
        min_assurance_level=None,
        independent_execution_required=False,
    )

    freshness = FreshnessPolicy(max_evidence_age_seconds=900, require_env_digest_match=True)
    base_kwargs = dict(
        version="1.0.0",
        name="CriticalAuth",
        goal=Goal(description="Auth", success_criteria=["pass"]),
        non_goals=["b", "a"],
        risk_tier=RiskTier.CRITICAL,
        autonomy_tier=AutonomyTier.MANUAL,
        scope_boundaries=["z", "a"],
        invariants=Invariants(security=["inv2", "inv1"]),
        constraints=Constraints(operational=["c2", "c1"]),
        tool_permissions=ToolPermissions(allowed=["t2", "t1"], denied=["d2", "d1"]),
        memory_requirements=[MemoryRequirement(scope="s", purpose="p")],
        model_budget=ModelBudget(max_tokens=1000, max_cost=1.0, max_loop_depth=2),
        release_requirements=ReleaseRequirements(required=True, gates=["g2", "g1"]),
        telemetry_requirements=TelemetryRequirements(required=True, signals=["s2", "s1"]),
        assurance_level=AssuranceLevel.A4_RESTRICTED,
        freshness_policy=freshness,
    )

    eip_one = EIP(proof_obligations=[po_a, po_b, po_c, po_d], **base_kwargs)
    eip_two = EIP(proof_obligations=[po_d, po_c, po_a, po_b], **base_kwargs)

    norm_one = normalize_eip(eip_one)
    norm_two = normalize_eip(eip_two)

    assert norm_one == norm_two
    assert norm_one["assurance_level"] == "A4_RESTRICTED"
    assert norm_one["freshness_policy"]["max_evidence_age_seconds"] == 900
    assert [
        (item["min_assurance_level"], item["independent_execution_required"])
        for item in norm_one["proof_obligations"]
    ] == [
        (None, False),
        (None, True),
        ("A2_INDEPENDENT", False),
        ("A3_ATTESTED", True),
    ]


def test_eip_schema_conformance_and_backward_compatibility():
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

    assert "critical" in schema["properties"]["risk_tier"]["enum"]
    assert "assurance_level" in schema["properties"]
    assert "freshness_policy" in schema["properties"]
    assert "assurance_level" not in schema["required"]
    assert "freshness_policy" not in schema["required"]
    assert set(schema["properties"].keys()) == set(EIP.model_fields.keys())

    po_schema = schema["properties"]["proof_obligations"]["items"]
    assert "min_assurance_level" in po_schema["properties"]
    assert "independent_execution_required" in po_schema["properties"]
    assert "min_assurance_level" not in po_schema["required"]
    assert "independent_execution_required" not in po_schema["required"]

    jsonschema = pytest.importorskip("jsonschema")

    legacy_payload = _minimal_eip_payload()
    jsonschema.validate(instance=legacy_payload, schema=schema)

    legacy_dumped = EIP(**legacy_payload).model_dump(mode="json")
    jsonschema.validate(instance=legacy_dumped, schema=schema)

    critical_payload = _minimal_eip_payload(
        risk_tier="critical",
        assurance_level="A4_RESTRICTED",
        freshness_policy={
            "max_evidence_age_seconds": 900,
            "require_head_commit_match": True,
            "require_tree_hash_match": True,
            "require_env_digest_match": True,
            "invalidate_on_dependency_change": True,
        },
        proof_obligations=[
            {
                "id": "po-sec",
                "kind": "security",
                "description": "Independent security verification",
                "min_assurance_level": "A4_RESTRICTED",
                "independent_execution_required": True,
            }
        ],
    )
    jsonschema.validate(instance=critical_payload, schema=schema)
    critical_dumped = EIP(**critical_payload).model_dump(mode="json")
    jsonschema.validate(instance=critical_dumped, schema=schema)
