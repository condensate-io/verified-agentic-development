from typing import Dict, Any

class FeatureFlags:
    """Simple feature flag abstraction for rollout control."""
    def __init__(self):
        self._flags: Dict[str, bool] = {}

    def set_flag(self, flag_name: str, is_enabled: bool):
        self._flags[flag_name] = is_enabled

    def is_enabled(self, flag_name: str, default: bool = False) -> bool:
        return self._flags.get(flag_name, default)

flag_manager = FeatureFlags()
