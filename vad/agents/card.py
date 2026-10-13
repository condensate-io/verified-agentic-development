"""Agent Card & Lifecycle Artifact Contracts.

Defines the core AgentCard metadata model, capability boundaries,
and artifact rendering methods for EIPs, verifier reports, and post-mortems.
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from vad.agents.lifecycle_prompts import (
    ADVERSARIAL_VERIFIER_TEMPLATE,
    EIP_11_SECTION_TEMPLATE,
    INCIDENT_TRIAGE_SKELETON,
    OBSERVABILITY_FINDINGS_TEMPLATE,
    PHASE_3_5_REPORT_TEMPLATE,
    POST_MORTEM_TEMPLATE,
)


class AgentCapabilities(BaseModel):
    tools: List[str] = Field(default_factory=list)
    model_tier: str = "tier1"
    memory_scope: str = "agent_local"
    max_loop_depth: int = 5
    requires_human_gate: bool = False
    allowed_subagents: List[str] = Field(default_factory=list)


class AgentCard(BaseModel):
    name: str
    description: str
    role: str = "general_agent"
    capabilities: AgentCapabilities
    
    # Creator and approver governance metadata
    builder: str
    approver: str
    version: str = "1.0.0"
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    # Prompt libraries and behavioral specifications
    system_prompt: str = ""
    prompt_templates: Dict[str, str] = Field(default_factory=dict)
    invariants: List[str] = Field(default_factory=list)
    proof_obligations: List[str] = Field(default_factory=list)
    tags: List[str] = Field(default_factory=list)
    
    def to_dict(self) -> Dict[str, Any]:
        return self.model_dump()

    def to_markdown(self) -> str:
        """Renders a comprehensive, publication-ready markdown Agent Card."""
        tools_str = ", ".join(f"`{t}`" for t in self.capabilities.tools) if self.capabilities.tools else "None"
        invariants_list = "\n".join(f"- `{inv}`" for inv in self.invariants) if self.invariants else "- None declared"
        proofs_list = "\n".join(f"- `{po}`" for po in self.proof_obligations) if self.proof_obligations else "- None declared"

        return f"""# Agent Card: {self.name}

## 1. Overview & Identity
- **Name**: `{self.name}`
- **Role**: `{self.role}`
- **Version**: `{self.version}`
- **Builder**: `{self.builder}`
- **Approver**: `{self.approver}`
- **Created**: `{self.created_at}`
- **Description**: {self.description}

## 2. Capabilities & Isolation Boundaries
- **Model Tier**: `{self.capabilities.model_tier}`
- **Memory Scope**: `{self.capabilities.memory_scope}`
- **Granted Tools**: {tools_str}
- **Loop Depth Cap**: `{self.capabilities.max_loop_depth}`
- **Requires Human Gate**: `{'Yes' if self.capabilities.requires_human_gate else 'No'}`
- **Delegation Allowed Subagents**: {', '.join(self.capabilities.allowed_subagents) or 'None'}

## 3. Mandatory Invariants
{invariants_list}

## 4. Proof Obligations
{proofs_list}

## 5. System Prompt Specification
```markdown
{self.system_prompt or "No custom prompt registered."}
```
"""

    # Template Rendering Helpers
    @staticmethod
    def render_eip_template(**kwargs) -> str:
        defaults = {
            "eip_id": "EIP-001",
            "version": "1.0.0",
            "title": "Untitled EIP",
            "author": "intent-steward",
            "status": "ready",
            "risk_tier": "moderate",
            "autonomy_tier": "assisted",
            "problem_statement": "Problem description here.",
            "goal_1": "Primary delivery goal",
            "criterion_1": "All tests pass",
            "goal_2": "Secondary goal",
            "criterion_2": "Zero lint errors",
            "non_goal_1": "Scope out of bounds",
            "scope_boundaries": "src/*",
            "invariant_functional": "No regression in core performance",
            "threats": "Injection or unconstrained execution",
            "max_tokens": 100000,
            "max_cost": 5.0,
            "max_loop_depth": 5,
            "feature_flag": "feat_new_capability",
            "open_questions": "- None",
            "sign_off_steward": "Approved",
            "sign_off_verifier": "Approved",
        }
        defaults.update(kwargs)
        return EIP_11_SECTION_TEMPLATE.format(**defaults)

    @staticmethod
    def render_verdict_template(**kwargs) -> str:
        defaults = {
            "artifact_id": "artifact-001",
            "commit_sha": "HEAD",
            "verifier_harness": "copilot-verifier",
            "verdict": "APPROVED",
            "critical_findings": "- None. Zero blocking vulnerabilities detected.",
            "warning_findings": "- None.",
            "info_findings": "- Clean formatting.",
            "inv_01_status": "PASS",
            "inv_01_evidence": "All credential regex scans passed",
            "inv_02_status": "PASS",
            "inv_02_evidence": "Reviewer harness distinct from builder",
            "inv_03_status": "PASS",
            "inv_03_evidence": "Hypothesis 100 examples evaluated",
            "inv_04_status": "PASS",
            "inv_04_evidence": "JSON Schema validated",
            "dsse_digest": "a1b2c3d4e5f6...",
            "attested_by": "adversarial-verifier",
        }
        defaults.update(kwargs)
        return ADVERSARIAL_VERIFIER_TEMPLATE.format(**defaults)

    @staticmethod
    def render_post_mortem_template(**kwargs) -> str:
        defaults = {
            "incident_id": "INC-001",
            "severity": "SEV-2",
            "start_time": "2026-10-13T00:00:00Z",
            "resolved_time": "2026-10-13T01:30:00Z",
            "ttd": "10 minutes",
            "ttm": "45 minutes",
            "executive_summary": "Service degradation occurred due to unexpected input payload.",
            "root_cause_analysis": "Missing schema validation on nested JSON object allowed malformed data.",
            "timeline": "- 00:00 Incident started\n- 00:10 Alarm triggered\n- 00:45 Mitigated",
            "new_inv_id": "04",
            "new_invariant_statement": "All incoming payloads must pass JSON Schema validation before dispatch.",
            "new_po_id": "05",
            "new_proof_obligation": "API fuzz test against malformed payloads",
            "test_command": "pytest tests/test_api_fuzz.py",
            "affected_skill": "work_intake",
            "skill_caveat": "Payloads missing required fields must be rejected at ingress.",
            "skill_guard_update": "Added check_payload_schema pre_tool guard.",
            "dor_update": "Mandatory schema fixture required in EIP.",
            "pre_tool_update": "PreToolUse enforces schema presence.",
        }
        defaults.update(kwargs)
        return POST_MORTEM_TEMPLATE.format(**defaults)

    @staticmethod
    def render_incident_skeleton(**kwargs) -> str:
        defaults = {
            "severity": "SEV-1",
            "feature_flag": "flag_core_engine",
            "commander": "Incident Commander",
            "ops_lead": "Operations Lead",
            "investigator": "Investigator Lead",
            "comms_lead": "Comms Lead",
            "containment_log": "- 00:05 Circuit breaker activated\n- 00:10 Feature flag disabled",
        }
        defaults.update(kwargs)
        return INCIDENT_TRIAGE_SKELETON.format(**defaults)

    @staticmethod
    def render_observability_findings(**kwargs) -> str:
        defaults = {
            "window_start": "2026-10-13T00:00:00Z",
            "window_end": "2026-10-13T01:00:00Z",
            "service_name": "vad-router",
            "sample_count": 5000,
            "p95_latency": 120,
            "p95_baseline": 95,
            "latency_drift": 26.3,
            "error_rate": 0.02,
            "error_threshold": 0.1,
            "tokens_per_sec": 450,
            "token_ceiling": 1000,
            "log_signatures": "Zero fatal exceptions; minor timeouts on cold starts.",
            "trace_bottlenecks": "Database connection acquisition took 40ms.",
            "slo_target": "99.9% availability",
            "burn_rate": 0.8,
            "remaining_budget": 98.4,
            "recommendations": "Tune connection pool size from 10 to 25.",
        }
        defaults.update(kwargs)
        return OBSERVABILITY_FINDINGS_TEMPLATE.format(**defaults)

    @staticmethod
    def render_integration_report(**kwargs) -> str:
        defaults = {
            "sandbox_dir": "/tmp/sandbox-vad-99",
            "start_time": "2026-10-13T00:00:00Z",
            "res_01": "PASS",
            "lat_01": 22,
            "res_02": "PASS",
            "lat_02": 35,
            "res_03": "PASS",
            "lat_03": 14,
            "integration_status": "PASS",
            "passed_count": 3,
            "total_count": 3,
            "teardown_executed": "True",
            "containers_pruned": "3 containers stopped and removed",
            "volumes_removed": "All ephemeral volumes removed",
        }
        defaults.update(kwargs)
        return PHASE_3_5_REPORT_TEMPLATE.format(**defaults)
