from vad.feedback.analyzer import FeedbackAnalyzer, Incident
from vad.telemetry.otel import setup_telemetry, get_tracer, get_meter

def test_assert_telemetry_linked_feedback_record_generated():
    # Simulate setup telemetry (just to ensure it doesn't crash)
    setup_telemetry("test-service")
    
    analyzer = FeedbackAnalyzer()
    record = analyzer.generate_feedback_record(source="telemetry_pipeline", data={"trace_id": "12345", "metric": "cpu_high"})
    
    assert record.source == "telemetry_pipeline"
    assert record.data["trace_id"] == "12345"
    assert record in analyzer.feedback_records

def test_invariant_update_proposal_generation():
    analyzer = FeedbackAnalyzer()
    
    # Simulate incident patterns
    analyzer.log_incident(Incident(issue_type="memory_leak", severity="high", description="OOM killed"))
    analyzer.log_incident(Incident(issue_type="memory_leak", severity="medium", description="High memory usage"))
    analyzer.log_incident(Incident(issue_type="memory_leak", severity="high", description="OOM killed again"))
    
    # Simulate SLO drift feedback
    analyzer.generate_feedback_record("slo_monitor", {"slo_drift": True})
    
    proposals = analyzer.propose_invariant_updates()
    
    assert any("resilient to 'memory_leak'" in p for p in proposals)
    assert any("prevent SLO drift" in p for p in proposals)
