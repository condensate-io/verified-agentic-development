import pytest
from vad.audit.cost_report import CostReportGenerator
from vad.audit.local_audit import LocalAuditLogger
from vad.plugins.claude_code.hooks.stop import StopHook


def test_stop_hook_permits_when_obligations_fulfilled(tmp_path):
    audit_db = tmp_path / "stop_audit.db"
    audit_jsonl = tmp_path / "stop_audit.jsonl"
    logger = LocalAuditLogger(sqlite_path=str(audit_db), jsonl_path=str(audit_jsonl))
    cost_gen = CostReportGenerator(price_per_1k_tokens=0.003)

    hook = StopHook(cost_generator=cost_gen, audit_logger=logger)

    payload = {
        "session_id": "sess-stop-ok",
        "agent_id": "claude-code",
        "tokens_used": 4000,
        "tool_calls": ["pytest", "read_file"],
        "proof_obligations": ["po-unit-tests", "po-lint"],
        "verified_proofs": ["po-unit-tests", "po-lint"],
    }

    res = hook.process(payload)
    assert res.allow_stop is True
    assert res.cost_report is not None
    assert res.cost_report.estimated_usd == 0.012

    # Verify audit event was logged
    events = logger.query_events(session_id="sess-stop-ok")
    assert len(events) == 1
    assert events[0].action == "CostReport"


def test_stop_hook_rejects_premature_stop_when_proofs_missing(tmp_path):
    audit_db = tmp_path / "stop_audit2.db"
    audit_jsonl = tmp_path / "stop_audit2.jsonl"
    logger = LocalAuditLogger(sqlite_path=str(audit_db), jsonl_path=str(audit_jsonl))
    hook = StopHook(audit_logger=logger)

    payload = {
        "session_id": "sess-stop-fail",
        "agent_id": "claude-code",
        "tokens_used": 1000,
        "tool_calls": [],
        "proof_obligations": ["po-security-review"],
        "verified_proofs": [],  # Missing!
    }

    res = hook.process(payload)
    assert res.allow_stop is False
    assert "po-security-review" in res.unfulfilled_obligations
    assert "Premature stop rejected" in res.reason
