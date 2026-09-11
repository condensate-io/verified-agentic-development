import json
import pytest
from pathlib import Path

from vad.connectors.github import GitHubConnector
from vad.connectors.models import ConnectorPlatform
from vad.connectors.registry import get_connector_registry, reset_connector_registry
from vad.server.app import VADApi
from vad.server.db.store import ServerStore


@pytest.fixture
def admin_api(tmp_path):
    reset_connector_registry()
    db_path = tmp_path / "admin_test.db"
    evidence_root = tmp_path / "evidence"
    evidence_root.mkdir()

    # Re-register GitHub connector class in case reset cleared it
    get_connector_registry().register_connector_class(
        ConnectorPlatform.GITHUB, GitHubConnector
    )

    api = VADApi(evidence_root=evidence_root, db_path=db_path)
    return api


def test_connectors_admin_crud_and_health(admin_api):
    # 1. Initially empty
    code, resp = admin_api.handle("GET", "/api/v1/connectors")
    assert code == 200
    assert resp["count"] == 0

    # 2. Register a connector
    payload = {
        "name": "gh-telephony",
        "platform": "github",
        "instance_url": "https://github.com",
        "webhook_secret": "my-secret-123",
        "project_whitelist": ["Five9/telephony"],
    }
    code, resp = admin_api.handle("POST", "/api/v1/connectors", body=payload)
    assert code == 201
    assert resp["status"] == "registered"
    assert resp["connector"]["name"] == "gh-telephony"

    # 3. List shows 1 connector
    code, resp = admin_api.handle("GET", "/api/v1/connectors")
    assert code == 200
    assert resp["count"] == 1
    assert resp["connectors"][0]["name"] == "gh-telephony"

    # 4. Health check
    code, resp = admin_api.handle("GET", "/api/v1/connectors/gh-telephony/health")
    assert code == 200
    assert resp["health"]["healthy"] is True

    # 5. Trigger sync
    code, resp = admin_api.handle("POST", "/api/v1/connectors/gh-telephony/sync")
    assert code == 200
    assert resp["status"] == "sync_completed"

    # 6. Unregister
    code, resp = admin_api.handle("DELETE", "/api/v1/connectors/gh-telephony")
    assert code == 200
    assert resp["status"] == "unregistered"

    # 7. Health check on deleted connector returns 404
    code, resp = admin_api.handle("GET", "/api/v1/connectors/gh-telephony/health")
    assert code == 404


def test_connectors_admin_validation_error(admin_api):
    # Missing required fields
    bad_payload = {"name": ""}
    code, resp = admin_api.handle("POST", "/api/v1/connectors", body=bad_payload)
    assert code == 400
    assert resp["error"] == "validation_error"
