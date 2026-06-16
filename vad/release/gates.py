from typing import Dict, List
from vad.release.flags import FeatureFlags, flag_manager

class RolloutGate:
    def __init__(self, metric_name: str, minimum_health_threshold: float):
        self.metric_name = metric_name
        self.minimum_health_threshold = minimum_health_threshold

class ReleaseManager:
    def __init__(self, flags: FeatureFlags = flag_manager):
        self.flags = flags
        self.gates: List[RolloutGate] = []

    def add_gate(self, gate: RolloutGate):
        self.gates.append(gate)

    def evaluate_release(self, candidate_name: str, metrics: Dict[str, float], has_telemetry: bool = True) -> bool:
        """
        Evaluates whether a release candidate can be deployed based on telemetry,
        feature flags, and health threshold metrics.
        """
        # Missing telemetry rejection for production candidates.
        if not has_telemetry:
            raise ValueError(f"Release candidate '{candidate_name}' rejected: Missing telemetry integration.")

        # Flag-off prevents deploy path.
        if not self.flags.is_enabled(candidate_name):
            raise ValueError(f"Release candidate '{candidate_name}' rejected: Feature flag is off.")

        # Health threshold regression triggers rollback.
        for gate in self.gates:
            if gate.metric_name in metrics:
                val = metrics[gate.metric_name]
                if val < gate.minimum_health_threshold:
                    raise RuntimeError(f"Rollback triggered: '{gate.metric_name}' health ({val}) is below threshold ({gate.minimum_health_threshold}).")

        return True
