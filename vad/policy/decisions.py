from pydantic import BaseModel, Field


class PolicyDecision(BaseModel):
    allow: bool
    reasons: list[str] = Field(default_factory=list)
    denials: list[str] = Field(default_factory=list)
    requires_human: bool = False


class PolicyEvaluationError(RuntimeError):
    pass
