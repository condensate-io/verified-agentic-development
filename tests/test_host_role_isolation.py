from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from vad.control_plane.clients import (
    ClientConnectionMode,
    ClientManifest,
    ClientTrustState,
    ClientType,
)
from vad.control_plane.mcp_gateway import (
    GatewayToolVisibilityRequest,
    MCPGateway,
    authorize_gateway_tool_call,
    authorize_host_session,
)
from vad.control_plane.mcp_work_tools import (
    WorkIntakeToolHandler,
    authorize_work_intake_tool_call,
    handle_work_intake_tool_call,
)
from vad.control_plane.sessions import (
    HostRole,
    HostSessionClaims,
    HostSessionManager,
    HostSessionToken,
)
from vad.control_plane.work_items import WorkItem, WorkItemGovernance, WorkItemStatus
from vad.server.api.clients import ClientRegistryService
from vad.server.api.work_items import WorkItemService
from vad.server.db.store import ServerStore


_TEST_SECRET = b"vad-host-role-isolation-secret-key-01"
_HOST_TYPES = ["codex", "claude_code", "cursor", "generic_mcp"]


def _base_now() -> datetime:
    return datetime(2026, 11, 24, 12, 0, 0, tzinfo=timezone.utc)


def _issue_token(
    manager: HostSessionManager,
    *,
    host_type: str,
    role: HostRole,
    client_id: str | None = None,
    work_item_id: str | None = "wi-101",
    fence_token: int = 2,
    now: datetime | None = None,
    ttl_minutes: int = 15,
) -> HostSessionToken:
    issued_at = now or _base_now()
    resolved_client = client_id or f"{host_type}-{role.value}-1"
    claims = HostSessionClaims(
        session_id=f"sess-{host_type}-{role.value}",
        client_id=resolved_client,
        host_type=host_type,
        role=role,
        actor=f"{resolved_client}-actor",
        work_item_id=work_item_id,
        fence_token=fence_token,
        issued_at=issued_at,
        expires_at=issued_at + timedelta(minutes=ttl_minutes),
    )
    return manager.issue(claims)


@pytest.mark.parametrize("host_type", _HOST_TYPES)
def test_builder_host_session_role_isolation_across_hosts(host_type: str):
    manager = HostSessionManager(_TEST_SECRET)
    now = _base_now()
    token = _issue_token(manager, host_type=host_type, role=HostRole.BUILDER, now=now)
    gateway = MCPGateway(session_manager=manager, require_host_session=True)
    work_handler = WorkIntakeToolHandler(session_manager=manager, require_host_session=True)

    for allowed_action in (
        "discover_work",
        "claim_work",
        "report_progress",
        "submit_diff_proposal",
        "submit_builder_claim",
    ):
        decision = authorize_host_session(
            manager,
            token,
            action=allowed_action,
            work_item_id="wi-101",
            min_fence_token=2,
            now=now + timedelta(minutes=1),
        )
        assert decision.allow is True, f"{host_type} builder should allow {allowed_action}: {decision.denials}"

        gw_decision = gateway.authorize_session(
            token.model_dump(mode="json"),
            action=allowed_action,
            work_item_id="wi-101",
            min_fence_token=2,
            now=now + timedelta(minutes=1),
        )
        assert gw_decision.allow is True

        wt_decision = work_handler.authorize_call(
            allowed_action,
            {"work_item_id": "wi-101", "host_session": token, "fence_token": 2},
            min_fence_token=2,
            now=now + timedelta(minutes=1),
        )
        assert wt_decision.allow is True

    for denied_action in (
        "submit_verification_result",
        "approve_release",
        "promote_lesson",
    ):
        decision = authorize_host_session(
            manager,
            token,
            action=denied_action,
            work_item_id="wi-101",
            min_fence_token=2,
            now=now + timedelta(minutes=1),
        )
        assert decision.allow is False, f"{host_type} builder must deny {denied_action}"
        assert decision.denials

        wt_resp = work_handler.handle(
            denied_action,
            {"work_item_id": "wi-101", "host_session": token.model_dump(mode="json"), "fence_token": 2},
            now=now + timedelta(minutes=1),
        )
        assert wt_resp["isError"] is True
        assert wt_resp["policy_decision"]["allow"] is False


@pytest.mark.parametrize("host_type", _HOST_TYPES)
def test_verifier_host_session_role_isolation_across_hosts(host_type: str):
    manager = HostSessionManager(_TEST_SECRET)
    now = _base_now()
    token = _issue_token(manager, host_type=host_type, role=HostRole.VERIFIER, now=now)
    gateway = MCPGateway(session_manager=manager, require_host_session=True)
    work_handler = WorkIntakeToolHandler(session_manager=manager, require_host_session=True)

    allowed = authorize_host_session(
        manager,
        token,
        action="submit_verification_result",
        work_item_id="wi-101",
        min_fence_token=2,
        now=now + timedelta(minutes=1),
    )
    assert allowed.allow is True

    proof_audit = gateway.authorize_tool_call(
        "run_proofs",
        GatewayToolVisibilityRequest(
            client_id=token.claims.client_id,
            host_session=token,
            work_item_id="wi-101",
            min_fence_token=2,
        ),
        now=now + timedelta(minutes=1),
    )
    assert proof_audit.visible is True

    for denied_action in ("submit_diff_proposal", "approve_release", "mutate_workspace"):
        denied = authorize_host_session(
            manager,
            token,
            action=denied_action,
            work_item_id="wi-101",
            min_fence_token=2,
            now=now + timedelta(minutes=1),
        )
        assert denied.allow is False
        assert denied.denials

        wt_decision = work_handler.authorize_call(
            denied_action,
            {"work_item_id": "wi-101", "host_session": token, "fence_token": 2},
            now=now + timedelta(minutes=1),
        )
        assert wt_decision.allow is False

    patch_audit = gateway.authorize_tool_call(
        "repo_patch",
        GatewayToolVisibilityRequest(
            client_id=token.claims.client_id,
            approved_high_risk_tools=("repo_patch",),
            host_session=token,
            work_item_id="wi-101",
        ),
        now=now + timedelta(minutes=1),
    )
    assert patch_audit.visible is False


@pytest.mark.parametrize("host_type", _HOST_TYPES)
def test_release_guardian_host_session_isolation_across_hosts(host_type: str):
    manager = HostSessionManager(_TEST_SECRET)
    now = _base_now()
    token = _issue_token(manager, host_type=host_type, role=HostRole.RELEASE_GUARDIAN, now=now)
    gateway = MCPGateway(session_manager=manager, require_host_session=True)
    work_handler = WorkIntakeToolHandler(session_manager=manager, require_host_session=True)

    for allowed_action in ("approve_release", "promote_lesson"):
        decision = authorize_host_session(
            manager,
            token,
            action=allowed_action,
            work_item_id="wi-101",
            min_fence_token=2,
            now=now + timedelta(minutes=1),
        )
        assert decision.allow is True

        resp = work_handler.handle(
            allowed_action,
            {"work_item_id": "wi-101", "host_session": token.model_dump(mode="json")},
            now=now + timedelta(minutes=1),
        )
        assert resp["isError"] is False
        assert resp["policy_decision"]["allow"] is True

    sign_audit = gateway.authorize_tool_call(
        "sign_verify",
        GatewayToolVisibilityRequest(
            client_id=token.claims.client_id,
            approved_high_risk_tools=("sign_verify",),
            host_session=token,
            work_item_id="wi-101",
        ),
        now=now + timedelta(minutes=1),
    )
    assert sign_audit.visible is True

    for denied_action in ("claim_work", "submit_diff_proposal", "submit_verification_result"):
        denied = work_handler.authorize_call(
            denied_action,
            {"work_item_id": "wi-101", "host_session": token},
            now=now + timedelta(minutes=1),
        )
        assert denied.allow is False


def test_host_session_fail_closed_on_tamper_expiry_wrong_work_item_and_stale_fence():
    manager = HostSessionManager(_TEST_SECRET)
    now = _base_now()
    token = _issue_token(
        manager,
        host_type="codex",
        role=HostRole.BUILDER,
        work_item_id="wi-101",
        fence_token=2,
        now=now,
        ttl_minutes=10,
    )
    gateway = MCPGateway(session_manager=manager, require_host_session=True)
    work_handler = WorkIntakeToolHandler(session_manager=manager, require_host_session=True)

    # 1. Stale fence token (token.claims.fence_token < min_fence_token)
    stale = authorize_host_session(
        manager,
        token,
        action="claim_work",
        work_item_id="wi-101",
        min_fence_token=3,
        now=now + timedelta(minutes=1),
    )
    assert stale.allow is False
    assert "stale fence token" in stale.denials[0]

    stale_gw = gateway.validate_tool_call(
        {
            "name": "work_intake_claim",
            "arguments": {"work_item_id": "wi-101", "host_session": token.model_dump(mode="json")},
        },
        min_fence_token=3,
        now=now + timedelta(minutes=1),
    )
    assert stale_gw.allow is False

    stale_wt = work_handler.authorize_call(
        "work_intake_claim",
        {"work_item_id": "wi-101", "host_session": token, "fence_token": 1},
        now=now + timedelta(minutes=1),
    )
    assert stale_wt.allow is False

    # 2. Tampered signature and tampered claims
    tampered_sig = HostSessionToken(claims=token.claims, signature="forged-signature")
    assert authorize_host_session(
        manager, tampered_sig, action="claim_work", work_item_id="wi-101", now=now
    ).allow is False

    escalated_claims = token.claims.model_copy(update={"role": HostRole.RELEASE_GUARDIAN})
    tampered_claims = HostSessionToken(claims=escalated_claims, signature=token.signature)
    assert authorize_host_session(
        manager, tampered_claims, action="approve_release", work_item_id="wi-101", now=now
    ).allow is False

    # 3. Expired token
    expired = authorize_host_session(
        manager,
        token,
        action="claim_work",
        work_item_id="wi-101",
        min_fence_token=2,
        now=now + timedelta(minutes=10),
    )
    assert expired.allow is False
    assert "expired" in expired.denials[0]

    # 4. Wrong work_item_id
    wrong_wi = authorize_host_session(
        manager,
        token,
        action="claim_work",
        work_item_id="wi-999",
        min_fence_token=2,
        now=now + timedelta(minutes=1),
    )
    assert wrong_wi.allow is False
    assert "work_item_id mismatch" in wrong_wi.denials[0]

    # 5. Spoofed client_id or role escalation in request arguments
    spoofed_client = work_handler.authorize_call(
        "work_intake_claim",
        {
            "work_item_id": "wi-101",
            "client_id": "other-client",
            "host_session": token,
        },
        now=now + timedelta(minutes=1),
    )
    assert spoofed_client.allow is False
    assert "client_id mismatch" in spoofed_client.denials[0]

    spoofed_role = gateway.validate_tool_call(
        {
            "name": "sign_verify",
            "arguments": {
                "work_item_id": "wi-101",
                "role": "release_guardian",
                "host_session": token.model_dump(mode="json"),
            },
        },
        now=now + timedelta(minutes=1),
    )
    assert spoofed_role.allow is False


def test_require_host_session_fails_closed_when_omitted_and_preserves_legacy_when_disabled():
    manager = HostSessionManager(_TEST_SECRET)

    strict_gateway = MCPGateway(session_manager=manager, require_host_session=True)
    assert strict_gateway.authorize_session(None, action="claim_work").allow is False
    assert strict_gateway.validate_tool_call({"name": "work_intake_discover", "arguments": {}}).allow is False
    assert strict_gateway.authorize_tool_call("work_intake_discover", {"role": "developer"}).visible is False
    assert strict_gateway.filter_tools({"role": "developer"}).tools == ()

    strict_work = WorkIntakeToolHandler(session_manager=manager, require_host_session=True)
    assert strict_work.authorize_call("work_intake_discover", {}).allow is False
    denied_resp = strict_work.handle("work_intake_discover", {})
    assert denied_resp["isError"] is True
    assert denied_resp["policy_decision"]["allow"] is False

    assert authorize_gateway_tool_call(
        "work_intake_discover",
        GatewayToolVisibilityRequest(role="developer"),
        session_manager=manager,
        require_host_session=True,
    ).visible is False

    # Backward compatibility when session_manager=None and require_host_session=False
    legacy_gateway = MCPGateway()
    assert legacy_gateway.authorize_session(None, action="claim_work").allow is True
    assert legacy_gateway.authorize_tool_call(
        "work_intake_discover",
        GatewayToolVisibilityRequest(role="developer"),
    ).visible is True
    assert authorize_work_intake_tool_call("work_intake_discover", {}).allow is True


def test_work_intake_handler_end_to_end_with_store_and_lease_fence_enforcement(tmp_path):
    db_path = tmp_path / "work_isolation.sqlite3"
    store = ServerStore(db_path)

    for client_id, client_type in [
        ("codex-builder-1", ClientType.CODEX),
        ("cursor-builder-2", ClientType.CURSOR),
    ]:
        ClientRegistryService(store).register(
            ClientManifest(
                client_id=client_id,
                display_name=client_id,
                client_type=client_type,
                version="1.0.0",
                connection_mode=ClientConnectionMode.MCP,
                supported_capabilities=("developer",),
                workspace_root=tmp_path,
                trust_state=ClientTrustState.TRUSTED,
            )
        )

    WorkItemService(store).create(
        WorkItem(
            work_item_id="wi-e2e-1",
            run_id="run-e2e",
            title="Scoped MCP work item",
            description="Test end-to-end fenced host session isolation",
            role="developer",
            status=WorkItemStatus.QUEUED,
            governance=WorkItemGovernance(
                effort_type="feature",
                mees_estimate=85,
                token_budget=10000,
            ),
        ),
        actor="operator",
        role="operator",
    )

    manager = HostSessionManager(_TEST_SECRET)
    now = datetime.now(timezone.utc)
    codex_token = _issue_token(
        manager,
        host_type="codex",
        role=HostRole.BUILDER,
        client_id="codex-builder-1",
        work_item_id="wi-e2e-1",
        fence_token=2,
        now=now,
    )

    handler = WorkIntakeToolHandler(session_manager=manager, require_host_session=True)

    # 1. Legacy call without host_session when require_host_session=False still works
    legacy_discover = handle_work_intake_tool_call("work_intake_discover", {"db_path": db_path, "role": "developer"})
    assert legacy_discover["isError"] is False
    assert json.loads(legacy_discover["content"][0]["text"])["count"] == 1

    # 2. Claim using scoped Codex builder session
    claim_resp = handler.claim(
        {
            "db_path": db_path,
            "work_item_id": "wi-e2e-1",
            "host_session": codex_token.model_dump(mode="json"),
            "fence_token": 2,
        }
    )
    assert claim_resp["isError"] is False
    claim_payload = json.loads(claim_resp["content"][0]["text"])
    lease_id = claim_payload["lease_id"]

    # Simulate a lease takeover that bumps the stored lease fence_token to 3
    lease = store.load_task_lease("wi-e2e-1")
    store.save_task_lease(lease.model_copy(update={"fence_token": 3, "client_id": "cursor-builder-2"}))

    # 3. Stale Codex builder token (fence_token=2 < stored lease fence_token=3) is rejected!
    stale_progress = handler.report_progress(
        {
            "db_path": db_path,
            "work_item_id": "wi-e2e-1",
            "lease_id": lease_id,
            "message": "stale progress update",
            "host_session": codex_token,
        }
    )
    assert stale_progress["isError"] is True
    assert "stale fence token" in stale_progress["content"][0]["text"]

    # 4. New Cursor builder token with fence_token=3 succeeds
    cursor_token = _issue_token(
        manager,
        host_type="cursor",
        role=HostRole.BUILDER,
        client_id="cursor-builder-2",
        work_item_id="wi-e2e-1",
        fence_token=3,
        now=now,
    )
    proof_resp = handler.submit_proof(
        {
            "db_path": db_path,
            "work_item_id": "wi-e2e-1",
            "lease_id": lease_id,
            "evidence_digest": "b" * 64,
            "test_summary": "All isolated tests passed",
            "host_session": cursor_token,
            "fence_token": 3,
        }
    )
    assert proof_resp["isError"] is False
    assert json.loads(proof_resp["content"][0]["text"])["status"] == "verifying"
