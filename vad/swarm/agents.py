from enum import Enum

from pydantic import BaseModel, Field, model_validator


class AgentRole(str, Enum):
    PLANNER = "planner"
    BUILDER = "builder"
    VERIFIER = "verifier"
    AUDITOR = "auditor"
    RELEASE_GUARDIAN = "release_guardian"


ROLE_CAPABILITIES = {
    AgentRole.PLANNER: {"plan", "decompose"},
    AgentRole.BUILDER: {"modify_code", "run_tests"},
    AgentRole.VERIFIER: {"run_tests", "verify"},
    AgentRole.AUDITOR: {"audit_evidence", "verify"},
    AgentRole.RELEASE_GUARDIAN: {"approve_release", "rollback"},
}


class AgentCard(BaseModel):
    agent_id: str = Field(min_length=1)
    role: AgentRole
    capabilities: list[str] = Field(min_length=1)
    model_tiers: list[str] = Field(min_length=1)
    memory_scopes: list[str] = Field(default_factory=list)
    autonomy_limit: int = Field(ge=0, le=5)

    @model_validator(mode="after")
    def validate_role_capabilities(self):
        allowed = ROLE_CAPABILITIES[self.role]
        unsupported = set(self.capabilities) - allowed
        if unsupported:
            raise ValueError(f"capabilities not allowed for role {self.role.value}: {', '.join(sorted(unsupported))}")
        if self.role in {AgentRole.BUILDER, AgentRole.RELEASE_GUARDIAN} and self.autonomy_limit == 0:
            raise ValueError(f"{self.role.value} requires nonzero autonomy limit")
        return self
