import pytest
import os
from vad.evidence.bundle import EvidenceBundle
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
