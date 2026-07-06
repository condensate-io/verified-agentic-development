import json
import os
from datetime import datetime
from vad.evidence.bundle import EvidenceBundle
from vad.agents.card import AgentCard
from vad.policy.engine import PolicyEngine

class ProvenanceEmitter:
    def __init__(self, policy_path: str = "policies/vad.rego", output_dir: str = "provenance_artifacts"):
        self.policy_engine = PolicyEngine(policy_dir=os.path.dirname(policy_path))
        self.policy_path = policy_path
        self.output_dir = output_dir

    def emit(self, agent_card: AgentCard, run_evidence=None) -> str:
        """
        Validates the agent card against policies, builds an evidence bundle,
        and emits a provenance record.
        """
        # 1. Evaluate build policy
        input_data = {
            "action": "build",
            "builder": agent_card.builder,
            "approver": agent_card.approver
        }
        
        is_denied = self.policy_engine.evaluate(self.policy_path, "data.vad.policy.deny_builder_self_approval", input_data)
        if is_denied:
            raise ValueError(f"Build failed: Builder {agent_card.builder} cannot self-approve.")
            
        is_allowed = self.policy_engine.evaluate(self.policy_path, "data.vad.policy.allow", input_data)
        if not is_allowed:
            raise ValueError("Build failed: Policy denies build.")

        linked_evidence_digest = None
        if run_evidence is not None:
            linked_evidence_digest = EvidenceBundle(run_evidence).compute_hash()

        # 2. Bundle evidence
        evidence_data = {
            "timestamp": datetime.utcnow().isoformat(),
            "agent_card": agent_card.to_dict(),
            "policy_decisions": {
                "build_allowed": is_allowed
            },
            "linked_evidence_digest": linked_evidence_digest,
        }
        
        bundle = EvidenceBundle(evidence_data)
        evidence_hash = bundle.compute_hash()
        
        record = {
            "digest": evidence_hash,
            "linked_evidence_digest": linked_evidence_digest,
            "bundle_data": evidence_data
        }
        
        if not os.path.exists(self.output_dir):
            os.makedirs(self.output_dir)
            
        filepath = os.path.join(self.output_dir, f"provenance_{evidence_hash}.json")
        with open(filepath, "w") as f:
            f.write(json.dumps(record, indent=2))
            
        return filepath
