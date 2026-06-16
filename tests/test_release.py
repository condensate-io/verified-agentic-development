import pytest
from vad.release.flags import FeatureFlags
from vad.release.gates import ReleaseManager, RolloutGate

def test_flag_off_prevents_deploy_path():
    flags = FeatureFlags()
    flags.set_flag("new_feature", False)
    
    manager = ReleaseManager(flags=flags)
    
    with pytest.raises(ValueError, match="rejected: Feature flag is off"):
        manager.evaluate_release("new_feature", metrics={"health": 1.0})

def test_health_threshold_regression_triggers_rollback():
    flags = FeatureFlags()
    flags.set_flag("candidate_app", True)
    
    manager = ReleaseManager(flags=flags)
    manager.add_gate(RolloutGate(metric_name="latency", minimum_health_threshold=0.95))
    
    with pytest.raises(RuntimeError, match="Rollback triggered: 'latency' health"):
        manager.evaluate_release("candidate_app", metrics={"latency": 0.90})

def test_missing_telemetry_rejection():
    flags = FeatureFlags()
    flags.set_flag("candidate_app", True)
    
    manager = ReleaseManager(flags=flags)
    
    with pytest.raises(ValueError, match="rejected: Missing telemetry integration"):
        manager.evaluate_release("candidate_app", metrics={"latency": 0.99}, has_telemetry=False)

def test_execute_flagged_release():
    flags = FeatureFlags()
    flags.set_flag("sample_app", True)
    
    manager = ReleaseManager(flags=flags)
    manager.add_gate(RolloutGate(metric_name="latency", minimum_health_threshold=0.95))
    manager.add_gate(RolloutGate(metric_name="error_rate", minimum_health_threshold=0.99))
    
    # Should succeed
    result = manager.evaluate_release("sample_app", metrics={"latency": 0.98, "error_rate": 0.995}, has_telemetry=True)
    assert result is True
