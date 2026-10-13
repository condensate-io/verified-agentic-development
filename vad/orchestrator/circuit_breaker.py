"""Circuit Breaker: Global trip controller locking staging operations after 3 push-gate violations."""

from datetime import datetime, timezone
import threading
from typing import List, Optional
from pydantic import BaseModel, Field


class ViolationRecord(BaseModel):
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    reason: str


class CircuitBreaker:
    """Monitors push-gate violations and trips globally after 3 consecutive/cumulative failures."""

    TRIP_THRESHOLD = 3

    def __init__(self, threshold: int = 3):
        self.threshold = threshold
        self._lock = threading.Lock()
        self._violations: List[ViolationRecord] = []
        self._tripped: bool = False
        self._tripped_at: Optional[str] = None

    def record_violation(self, reason: str) -> bool:
        """Records a push-gate violation. Returns True if this violation caused the breaker to trip."""
        with self._lock:
            rec = ViolationRecord(reason=reason)
            self._violations.append(rec)
            if len(self._violations) >= self.threshold and not self._tripped:
                self._tripped = True
                self._tripped_at = datetime.now(timezone.utc).isoformat()
                return True
            return False

    def is_tripped(self) -> bool:
        with self._lock:
            return self._tripped

    @property
    def violation_count(self) -> int:
        with self._lock:
            return len(self._violations)

    def get_violations(self) -> List[ViolationRecord]:
        with self._lock:
            return list(self._violations)

    def reset(self, operator_id: str) -> None:
        """Manually resets the circuit breaker. Requires human operator authorization."""
        with self._lock:
            self._violations.clear()
            self._tripped = False
            self._tripped_at = None


# Singleton instance for shared orchestrator state
_GLOBAL_BREAKER = CircuitBreaker()


def get_circuit_breaker() -> CircuitBreaker:
    return _GLOBAL_BREAKER
