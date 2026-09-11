import pytest
from fastapi.testclient import TestClient
from vad.audit.local_audit import LocalAuditLogger, AuditEvent
from vad.ui.dashboard.analytics_api import create_app


def test_analytics_api_endpoints(tmp_path):
    db_file = tmp_path / "audit.db"
    logger = LocalAuditLogger(sqlite_path=str(db_file), jsonl_path=str(tmp_path / "audit.jsonl"))

    logger.log(AuditEvent(
        session_id="s1",
        agent_id="claude-code",
        action="execute_test",
        outcome="success",
        metadata={"tokens": 1000, "cost_usd": 0.003},
    ))
    logger.log(AuditEvent(
        session_id="s1",
        agent_id="copilot-worker",
        action="review_diff",
        outcome="success",
        metadata={"tokens": 500, "cost_usd": 0.0015},
    ))

    app = create_app(sqlite_path=str(db_file))
    client = TestClient(app)

    # 1. Cost endpoint
    res_cost = client.get("/metrics/cost")
    assert res_cost.status_code == 200
    data_cost = res_cost.json()
    assert data_cost["total_tokens"] == 1500
    assert data_cost["estimated_cost_usd"] == 0.0045

    # 2. ROI endpoint
    res_roi = client.get("/metrics/roi")
    assert res_roi.status_code == 200
    data_roi = res_roi.json()
    assert data_roi["engineering_hours_saved"] == 0.5
    assert data_roi["gross_savings_usd"] == 60.0
    assert data_roi["net_savings_usd"] > 59.0

    # 3. Utilization endpoint
    res_util = client.get("/metrics/agent-utilization")
    assert res_util.status_code == 200
    data_util = res_util.json()
    assert data_util["total_events"] == 2
    assert data_util["utilization"]["claude-code"] == 1
    assert data_util["utilization"]["copilot-worker"] == 1

    # 4. Trends endpoint
    res_trends = client.get("/metrics/trends")
    assert res_trends.status_code == 200
    data_trends = res_trends.json()
    assert len(data_trends["timeline"]) >= 1
