from enum import Enum

from pydantic import BaseModel, Field


class LoopStatus(str, Enum):
    INITIALIZED = "initialized"
    ASSESSED = "assessed"
    PLANNED = "planned"
    POLICY_CHECKED = "policy_checked"
    BUILDING = "building"
    VERIFYING = "verifying"
    RELEASING = "releasing"
    FEEDBACK = "feedback"
    PASSED = "passed"
    FAILED = "failed"
    BLOCKED = "blocked"
    NEEDS_HUMAN = "needs_human"


ALLOWED_TRANSITIONS = {
    LoopStatus.INITIALIZED: {LoopStatus.ASSESSED, LoopStatus.PLANNED, LoopStatus.BLOCKED},
    LoopStatus.ASSESSED: {LoopStatus.PLANNED, LoopStatus.NEEDS_HUMAN, LoopStatus.BLOCKED},
    LoopStatus.PLANNED: {LoopStatus.POLICY_CHECKED, LoopStatus.BLOCKED},
    LoopStatus.POLICY_CHECKED: {LoopStatus.BUILDING, LoopStatus.VERIFYING, LoopStatus.NEEDS_HUMAN, LoopStatus.BLOCKED},
    LoopStatus.BUILDING: {LoopStatus.VERIFYING, LoopStatus.FAILED, LoopStatus.BLOCKED},
    LoopStatus.VERIFYING: {LoopStatus.RELEASING, LoopStatus.FEEDBACK, LoopStatus.PASSED, LoopStatus.FAILED, LoopStatus.BLOCKED},
    LoopStatus.RELEASING: {LoopStatus.FEEDBACK, LoopStatus.PASSED, LoopStatus.FAILED, LoopStatus.BLOCKED},
    LoopStatus.FEEDBACK: {LoopStatus.PASSED, LoopStatus.FAILED, LoopStatus.BLOCKED},
    LoopStatus.PASSED: set(),
    LoopStatus.FAILED: set(),
    LoopStatus.BLOCKED: set(),
    LoopStatus.NEEDS_HUMAN: set(),
}


class LoopState(BaseModel):
    status: LoopStatus = LoopStatus.INITIALIZED
    history: list[LoopStatus] = Field(default_factory=lambda: [LoopStatus.INITIALIZED])

    def transition(self, next_status: LoopStatus) -> "LoopState":
        allowed = ALLOWED_TRANSITIONS[self.status]
        if next_status not in allowed:
            raise ValueError(f"Invalid loop transition: {self.status.value} -> {next_status.value}")
        return LoopState(status=next_status, history=[*self.history, next_status])
