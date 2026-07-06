import pytest
import json
import os
from vad.evidence.bundle import EvidenceBundle
from vad.evidence.bundle import AgentEvidence, EffortEvidence, EvidenceRef, RunEvidence, TokenEvidence, VerificationEvidence
from vad.evidence.provenance import ProvenanceEmitter
from vad.agents.card import AgentCard, AgentCapabilities

def test_deterministic_hashing():
    data1 = {"b": 2, "a": 1}
    data2 = {"a": 1, "b": 2}
    
    bundle1 = EvidenceBundle(data1)
    bundle2 = EvidenceBundle(data2)
    
    # Stable serialization should produce same bytes
    assert bundle1.serialize() == bundle2.serialize()
    
    # Hashes should match
    assert bundle1.compute_hash() == bundle2.compute_hash()

def test_tamper_detection():
    data = {"name": "TestAgent", "version": 1}
    bundle = EvidenceBundle(data)
    expected_hash = bundle.compute_hash()
    
    # Tampering with data
    tampered_data = {"name": "TestAgent", "version": 2}
    tampered_bundle = EvidenceBundle(tampered_data)
    
    assert tampered_bundle.is_tampered(expected_hash) is True

def test_typed_run_evidence_requires_core_fields():
    evidence = RunEvidence(
        run_id="run-1",
        created_at="2026-06-26T00:00:00",
        eip=EvidenceRef(path="eip.yaml", digest="abc"),
        proof_plan=EvidenceRef(path="proof.yaml", digest="def"),
        agents=AgentEvidence(builder="alice", verifier="bob"),
        verification=VerificationEvidence(passed=True),
        effort=EffortEvidence(
            effort_type="feature",
            mees=90,
            policy="pass",
            changed_files=1,
            line_delta=10,
            new_dependencies=0,
            complexity_delta=0,
            maintainability_delta=0,
        ),
        tokens=TokenEvidence(budget=1000, used=100),
        final_decision="passed",
    )

    bundle = EvidenceBundle(evidence)
    assert bundle.data["schema_version"] == "1.0.0"
    assert bundle.data["agents"]["builder"] == "alice"
    assert bundle.data["effort"]["mees"] == 90
    assert bundle.data["tokens"]["budget"] == 1000
    assert bundle.compute_hash()

def test_effort_record_requires_mees_metrics():
    with pytest.raises(ValueError):
        EffortEvidence(effort_type="feature", mees=90, policy="pass")

def test_mees_under_50_requires_human_or_blocked_decision():
    effort = EffortEvidence(
        effort_type="feature",
        mees=49,
        policy="block",
        changed_files=1,
        line_delta=12,
        new_dependencies=0,
        complexity_delta=0,
        maintainability_delta=0,
    )

    with pytest.raises(ValueError, match="MEES under 50"):
        RunEvidence(
            run_id="run-1",
            created_at="2026-06-26T00:00:00",
            eip=EvidenceRef(path="eip.yaml", digest="abc"),
            proof_plan=EvidenceRef(path="proof.yaml", digest="def"),
            agents=AgentEvidence(builder="alice", verifier="bob"),
            effort=effort,
            tokens=TokenEvidence(budget=1000),
            final_decision="passed",
        )

    blocked = RunEvidence(
        run_id="run-1",
        created_at="2026-06-26T00:00:00",
        eip=EvidenceRef(path="eip.yaml", digest="abc"),
        proof_plan=EvidenceRef(path="proof.yaml", digest="def"),
        agents=AgentEvidence(builder="alice", verifier="bob"),
        effort=effort,
        tokens=TokenEvidence(budget=1000),
        final_decision="needs_human",
    )
    assert blocked.final_decision == "needs_human"

def test_e2e_build_forbidden_capability():
    card = AgentCard(
        name="BadAgent",
        description="Agent with self-approval",
        capabilities=AgentCapabilities(
            tools=["shell"],
            model_tier="tier3",
            memory_scope="global"
        ),
        builder="alice",
        approver="alice"  # Self-approval
    )
    
    emitter = ProvenanceEmitter(policy_path="policies/vad.rego")
    
    with pytest.raises(ValueError, match="cannot self-approve"):
        emitter.emit(card)

def test_e2e_build_success(tmp_path):
    card = AgentCard(
        name="GoodAgent",
        description="Compliant agent",
        capabilities=AgentCapabilities(
            tools=["read_file"],
            model_tier="tier1",
            memory_scope="agent_local"
        ),
        builder="alice",
        approver="bob"
    )
    
    emitter = ProvenanceEmitter(policy_path="policies/vad.rego", output_dir=str(tmp_path))
    filepath = emitter.emit(card)
    
    assert os.path.exists(filepath)
    assert "provenance_" in filepath

def test_provenance_references_run_evidence_digest(tmp_path):
    card = AgentCard(
        name="GoodAgent",
        description="Compliant agent",
        capabilities=AgentCapabilities(
            tools=["read_file"],
            model_tier="tier1",
            memory_scope="agent_local"
        ),
        builder="alice",
        approver="bob"
    )
    run_evidence = RunEvidence(
        run_id="run-1",
        created_at="2026-06-26T00:00:00",
        eip=EvidenceRef(path="eip.yaml", digest="abc"),
        proof_plan=EvidenceRef(path="proof.yaml", digest="def"),
        agents=AgentEvidence(builder="alice", verifier="bob"),
        verification=VerificationEvidence(passed=True),
        effort=EffortEvidence(
            effort_type="feature",
            mees=90,
            policy="pass",
            changed_files=1,
            line_delta=10,
            new_dependencies=0,
            complexity_delta=0,
            maintainability_delta=0,
        ),
        tokens=TokenEvidence(budget=1000, used=100),
        final_decision="passed",
    )

    emitter = ProvenanceEmitter(policy_path="policies/vad.rego", output_dir=str(tmp_path))
    filepath = emitter.emit(card, run_evidence=run_evidence)

    record = json.loads(open(filepath).read())
    assert record["linked_evidence_digest"] == EvidenceBundle(run_evidence).compute_hash()
    assert record["bundle_data"]["linked_evidence_digest"] == record["linked_evidence_digest"]
