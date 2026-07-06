from pathlib import Path

import pytest
from pydantic import ValidationError

from vad.control_plane.clients import (
    ClientConnectionMode,
    ClientHeartbeat,
    ClientManifest,
    ClientRuntimeStatus,
    ClientTrustState,
    ClientType,
)


def test_client_manifest_captures_local_client_identity_and_capabilities(tmp_path):
    manifest = ClientManifest(
        client_id="codex-local",
        display_name="Codex",
        client_type=ClientType.CODEX,
        version="1.0.0",
        connection_mode=ClientConnectionMode.MCP,
        supported_capabilities=("repo_read", "tool_call", "repo_read"),
        workspace_root=tmp_path,
        trust_state=ClientTrustState.TRUSTED,
    )

    assert manifest.client_id == "codex-local"
    assert manifest.display_name == "Codex"
    assert manifest.supported_capabilities == ("repo_read", "tool_call")
    assert manifest.workspace_root == tmp_path
    assert manifest.trust_state == ClientTrustState.TRUSTED
    assert manifest.requested_policy_permissions == ()


def test_client_manifest_rejects_unknown_type_mode_and_trust_state(tmp_path):
    with pytest.raises(ValidationError):
        ClientManifest(
            client_id="unknown-client",
            display_name="Unknown",
            client_type="unknown",
            version="1.0.0",
            connection_mode="mcp",
            supported_capabilities=("repo_read",),
            workspace_root=tmp_path,
        )

    with pytest.raises(ValidationError):
        ClientManifest(
            client_id="unknown-client",
            display_name="Unknown",
            client_type="codex",
            version="1.0.0",
            connection_mode="telepathy",
            supported_capabilities=("repo_read",),
            workspace_root=tmp_path,
        )

    with pytest.raises(ValidationError):
        ClientManifest(
            client_id="unknown-client",
            display_name="Unknown",
            client_type="codex",
            version="1.0.0",
            connection_mode="mcp",
            supported_capabilities=("repo_read",),
            workspace_root=tmp_path,
            trust_state="superuser",
        )


def test_client_manifest_rejects_unsafe_identifiers_and_capabilities(tmp_path):
    with pytest.raises(ValidationError, match="client_id must not contain"):
        ClientManifest(
            client_id="../escape",
            display_name="Codex",
            client_type="codex",
            version="1.0.0",
            connection_mode="mcp",
            supported_capabilities=("repo_read",),
            workspace_root=tmp_path,
        )

    with pytest.raises(ValidationError, match="supported capabilities must not contain"):
        ClientManifest(
            client_id="codex-local",
            display_name="Codex",
            client_type="codex",
            version="1.0.0",
            connection_mode="mcp",
            supported_capabilities=("repo/read",),
            workspace_root=tmp_path,
        )


def test_client_capability_assertions_do_not_grant_policy_permissions(tmp_path):
    with pytest.raises(ValidationError, match="client capabilities do not grant policy permissions"):
        ClientManifest(
            client_id="codex-local",
            display_name="Codex",
            client_type="codex",
            version="1.0.0",
            connection_mode="mcp",
            supported_capabilities=("approve_release", "write_files"),
            requested_policy_permissions=("approve_release",),
            workspace_root=tmp_path,
            trust_state="trusted",
        )


def test_client_manifest_schema_contains_dashboard_registry_fields():
    schema = ClientManifest.json_schema()
    properties = schema["properties"]

    for field in [
        "client_id",
        "display_name",
        "client_type",
        "version",
        "connection_mode",
        "supported_capabilities",
        "workspace_root",
        "trust_state",
    ]:
        assert field in properties
    assert "requested_policy_permissions" in properties


def test_client_heartbeat_captures_runtime_status(tmp_path):
    heartbeat = ClientHeartbeat(
        client_id="codex-local",
        run_id="run-1",
        task_id="build",
        actor="codex",
        role="builder",
        status=ClientRuntimeStatus.ACTIVE,
    )

    assert heartbeat.client_id == "codex-local"
    assert heartbeat.run_id == "run-1"
    assert heartbeat.task_id == "build"
    assert heartbeat.status == ClientRuntimeStatus.ACTIVE


def test_client_heartbeat_rejects_unsafe_identifiers():
    with pytest.raises(ValidationError, match="client heartbeat identifiers"):
        ClientHeartbeat(
            client_id="../escape",
            actor="codex",
            role="builder",
        )
