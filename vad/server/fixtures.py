from __future__ import annotations

import json
from pathlib import Path

from vad.control_plane.clients import (
    ClientConnectionMode,
    ClientHeartbeat,
    ClientManifest,
    ClientTrustState,
    ClientType,
)
from vad.evidence.bundle import AgentEvidence, EffortEvidence, EvidenceRef, RunEvidence, TokenEvidence, VerificationEvidence
from vad.deploy.demo import run_failed_deployment_demo, run_signed_deployment_demo
from vad.policy.decisions import PolicyDecision
from vad.control_plane.events import ControlPlaneEvent, ControlPlaneEventKind, ControlPlaneEventStatus
from vad.control_plane.leases import TaskLeaseAcquireRequest
from vad.server.api.approvals import ApprovalRequest, ApprovalService
from vad.server.api.clients import ClientRegistryService
from vad.server.api.events import ControlPlaneEventService
from vad.server.api.leases import TaskLeaseService
from vad.server.db.store import ApprovalEvent, DashboardActivity, ServerStore


def seed_demo_store(db_path: Path) -> ServerStore:
    store = ServerStore(db_path)
    evidence = RunEvidence(
        run_id="demo-run",
        created_at="2026-07-01T00:00:00",
        eip=EvidenceRef(path="examples/eip/sample.yaml", digest="demo-eip-digest"),
        proof_plan=EvidenceRef(path="examples/proof/sample-proof-plan.yaml", digest="demo-proof-digest"),
        agents=AgentEvidence(builder="builder", verifier="verifier"),
        verification=VerificationEvidence(passed=True),
        effort=EffortEvidence(
            effort_type="feature",
            mees=90,
            policy="pass",
            changed_files=2,
            line_delta=12,
            new_dependencies=0,
            complexity_delta=0,
            maintainability_delta=0,
        ),
        tokens=TokenEvidence(budget=10000, used=900, remaining=9100),
        final_decision="passed",
    )
    run = store.save_run_evidence(evidence)

    activities = [
        DashboardActivity(activity_id="demo-swarm-plan", run_id=run.run_id, kind="swarm", status="active", client="Claude Code", actor="planner", role="planner", task_id="plan", summary="Planner assigned swarm task.", evidence_digest=run.evidence_digest),
        DashboardActivity(activity_id="demo-provider-budget", run_id=run.run_id, kind="provider", status="blocked", client="VSCode", actor="builder", role="builder", task_id="provider-budget", summary="Provider budget requires approval.", evidence_digest=run.evidence_digest),
        DashboardActivity(activity_id="demo-signature", run_id=run.run_id, kind="signing", status="passed", client="Codex", actor="auditor", role="auditor", task_id="sign", summary="Evidence signature verified.", evidence_digest=run.evidence_digest),
        DashboardActivity(activity_id="demo-deploy", run_id=run.run_id, kind="deployment", status="needs_human", client="Antigravity", actor="guardian", role="release_guardian", task_id="deploy", summary="Deployment apply awaits release approval.", evidence_digest=run.evidence_digest),
        DashboardActivity(activity_id="demo-cursor", run_id=run.run_id, kind="swarm", status="passed", client="Cursor", actor="verifier", role="verifier", task_id="verify", summary="Verifier completed proof review.", evidence_digest=run.evidence_digest),
        DashboardActivity(activity_id="demo-windsurf", run_id=run.run_id, kind="provider", status="passed", client="Windsurf", actor="router", role="provider", task_id="route", summary="Provider route selected within budget.", evidence_digest=run.evidence_digest),
        DashboardActivity(activity_id="demo-opencode", run_id=run.run_id, kind="signing", status="passed", client="OpenCode", actor="signer", role="signer", task_id="attest", summary="Run evidence attestation created.", evidence_digest=run.evidence_digest),
        DashboardActivity(activity_id="demo-generic-mcp", run_id=run.run_id, kind="deployment", status="active", client="Generic MCP", actor="deploy", role="deployer", task_id="rollout", summary="Fake rollout telemetry is being monitored.", evidence_digest=run.evidence_digest),
    ]
    for sequence, activity in enumerate(activities, start=1):
        _ingest_activity_event(store, activity, sequence=sequence)

    existing_approvals = store.list_approval_events(run.run_id)
    if not any(approval.approval_id == "demo-approval-denied" for approval in existing_approvals):
        store.save_approval_event(ApprovalEvent(
            approval_id="demo-approval-denied",
            run_id=run.run_id,
            actor="builder",
            action="approve_release",
            decision=PolicyDecision(allow=False, denials=["builder may not approve own run"], requires_human=True),
            evidence_digest=run.evidence_digest,
        ))
    return store


def seed_level3_demo_store(db_path: Path, fixture: Path = Path("examples/level3-demo")) -> ServerStore:
    store = ServerStore(db_path)
    evidence = RunEvidence(**json.loads((fixture / "run-evidence.json").read_text(encoding="utf-8")))
    run = store.save_run_evidence(evidence)
    dashboard = json.loads((fixture / "dashboard-seed.json").read_text(encoding="utf-8"))

    for approval_data in dashboard["approvals"]:
        approval_data["evidence_digest"] = run.evidence_digest
        approval_data["decision"] = PolicyDecision(**approval_data["decision"])
        if not _approval_exists(store, approval_data["run_id"], approval_data["approval_id"]):
            store.save_approval_event(ApprovalEvent(**approval_data))

    denied_approval = ApprovalEvent(
        approval_id="level3-demo-denied-self-approval",
        run_id=run.run_id,
        actor="claude-code-builder",
        action="approve_release",
        decision=PolicyDecision(allow=False, denials=["builder may not approve own run"], requires_human=True),
        evidence_digest=run.evidence_digest,
    )
    if not _approval_exists(store, run.run_id, denied_approval.approval_id):
        store.save_approval_event(denied_approval)

    next_sequence = 1
    for activity_data in dashboard["activity"]:
        activity_data["evidence_digest"] = run.evidence_digest
        _ingest_activity_event(store, DashboardActivity(**activity_data), sequence=next_sequence)
        next_sequence += 1

    demo_artifact_dir = db_path.parent / "level3-demo-artifacts"
    signed_demo = run_signed_deployment_demo(fixture, demo_artifact_dir)
    _ingest_activity_event(store, DashboardActivity(
        activity_id="level3-demo-signed-deployment",
        run_id=run.run_id,
        kind="deployment",
        status=signed_demo.final_decision,
        client="Generic MCP/A2A",
        actor="deployer",
        role="deployer",
        task_id="signed-deployment",
        summary="Signed fake deployment attestation verified.",
        evidence_digest=run.evidence_digest,
        details={"attestation_verified": signed_demo.attestation_verified},
    ), sequence=next_sequence)
    next_sequence += 1

    failed_demo = run_failed_deployment_demo(fixture, demo_artifact_dir, db_path=db_path)
    for activity_data in failed_demo.dashboard["activity"]:
        if activity_data["run_id"] == "level3-demo-failure":
            _ingest_activity_event(store, DashboardActivity(**activity_data), sequence=next_sequence)
            next_sequence += 1
    return store


SIMULATED_CLIENTS: tuple[dict[str, object], ...] = (
    {
        "client_id": "sim-codex",
        "display_name": "Codex",
        "client_type": ClientType.CODEX,
        "connection_mode": ClientConnectionMode.MCP,
        "actor": "codex-builder",
        "role": "builder",
    },
    {
        "client_id": "sim-antigravity",
        "display_name": "Antigravity",
        "client_type": ClientType.ANTIGRAVITY,
        "connection_mode": ClientConnectionMode.CLI,
        "actor": "antigravity-planner",
        "role": "planner",
    },
    {
        "client_id": "sim-claude-code",
        "display_name": "Claude Code",
        "client_type": ClientType.CLAUDE_CODE,
        "connection_mode": ClientConnectionMode.MCP,
        "actor": "claude-code-builder",
        "role": "builder",
    },
    {
        "client_id": "sim-vscode",
        "display_name": "VS Code",
        "client_type": ClientType.VS_CODE,
        "connection_mode": ClientConnectionMode.PLUGIN,
        "actor": "vscode-verifier",
        "role": "verifier",
    },
    {
        "client_id": "sim-windsurf",
        "display_name": "Windsurf",
        "client_type": ClientType.WINDSURF,
        "connection_mode": ClientConnectionMode.PLUGIN,
        "actor": "windsurf-auditor",
        "role": "auditor",
    },
    {
        "client_id": "sim-cursor",
        "display_name": "Cursor",
        "client_type": ClientType.CURSOR,
        "connection_mode": ClientConnectionMode.PLUGIN,
        "actor": "cursor-reviewer",
        "role": "verifier",
    },
    {
        "client_id": "sim-opencode",
        "display_name": "OpenCode",
        "client_type": ClientType.OPENCODE,
        "connection_mode": ClientConnectionMode.CLI,
        "actor": "opencode-release-guardian",
        "role": "release_guardian",
    },
    {
        "client_id": "sim-generic-mcp-a2a",
        "display_name": "Generic MCP/A2A",
        "client_type": ClientType.GENERIC_MCP,
        "connection_mode": ClientConnectionMode.MCP,
        "actor": "generic-mcp-deployer",
        "role": "release_guardian",
    },
)


def seed_multi_client_simulator_store(db_path: Path, workspace_root: Path | None = None) -> ServerStore:
    store = ServerStore(db_path)
    workspace = workspace_root or db_path.parent
    evidence = RunEvidence(
        run_id="multi-client-simulator",
        created_at="2026-07-03T00:00:00",
        eip=EvidenceRef(path="simulator/eip.yaml", digest="multi-client-simulator-eip"),
        proof_plan=EvidenceRef(path="simulator/proof-plan.yaml", digest="multi-client-simulator-proof"),
        agents=AgentEvidence(builder="multi-client-builder", verifier="multi-client-verifier"),
        verification=VerificationEvidence(passed=True),
        effort=EffortEvidence(
            effort_type="simulator",
            mees=88,
            policy="pass",
            changed_files=0,
            line_delta=0,
            new_dependencies=0,
            complexity_delta=0,
            maintainability_delta=0,
        ),
        tokens=TokenEvidence(budget=12000, used=1800, remaining=10200),
        final_decision="passed",
    )
    run = store.save_run_evidence(evidence)
    registry = ClientRegistryService(store)

    for index, client in enumerate(SIMULATED_CLIENTS, start=1):
        client_id = str(client["client_id"])
        display_name = str(client["display_name"])
        actor = str(client["actor"])
        role = str(client["role"])
        manifest = ClientManifest(
            client_id=client_id,
            display_name=display_name,
            client_type=client["client_type"],
            version="1.0.0",
            connection_mode=client["connection_mode"],
            supported_capabilities=(
                "client_registration",
                "heartbeat",
                "tool_call",
                "task_event",
                "proof_event",
                "signing_event",
                "fake_deployment_event",
            ),
            workspace_root=workspace,
            trust_state=ClientTrustState.TRUSTED,
        )
        registry.register(manifest)
        registry.heartbeat(ClientHeartbeat(
            client_id=client_id,
            run_id=run.run_id,
            task_id=f"sim-task-{index}",
            actor=actor,
            role=role,
            summary=f"{display_name} simulator heartbeat.",
        ))
        _ingest_simulator_event(
            store,
            client=client,
            run_id=run.run_id,
            task_id=f"sim-task-{index}",
            kind=ControlPlaneEventKind.TOOL_CALL_STARTED,
            status=ControlPlaneEventStatus.ACTIVE,
            summary=f"{display_name} started simulator tool call.",
            evidence_digest=run.evidence_digest,
        )
        _ingest_simulator_event(
            store,
            client=client,
            run_id=run.run_id,
            task_id=f"sim-task-{index}",
            kind=ControlPlaneEventKind.TOOL_CALL_FINISHED,
            status=ControlPlaneEventStatus.PASSED,
            summary=f"{display_name} finished simulator tool call.",
            evidence_digest=run.evidence_digest,
        )
        _ingest_simulator_event(
            store,
            client=client,
            run_id=run.run_id,
            task_id=f"sim-task-{index}",
            kind=ControlPlaneEventKind.WORK_ITEM,
            status=ControlPlaneEventStatus.PASSED,
            summary=f"{display_name} completed simulator task item.",
            evidence_digest=run.evidence_digest,
        )
        _ingest_simulator_event(
            store,
            client=client,
            run_id=run.run_id,
            task_id=f"sim-proof-{index}",
            kind=ControlPlaneEventKind.PROOF_STARTED,
            status=ControlPlaneEventStatus.ACTIVE,
            summary=f"{display_name} started simulator proof.",
            evidence_digest=run.evidence_digest,
        )
        _ingest_simulator_event(
            store,
            client=client,
            run_id=run.run_id,
            task_id=f"sim-proof-{index}",
            kind=ControlPlaneEventKind.PROOF_FINISHED,
            status=ControlPlaneEventStatus.PASSED,
            summary=f"{display_name} finished simulator proof.",
            evidence_digest=run.evidence_digest,
        )
        _ingest_simulator_event(
            store,
            client=client,
            run_id=run.run_id,
            task_id=f"sim-sign-{index}",
            kind=ControlPlaneEventKind.SIGNER_EVENT,
            status=ControlPlaneEventStatus.PASSED,
            summary=f"{display_name} emitted simulator signing evidence.",
            evidence_digest=run.evidence_digest,
            role="release_guardian",
        )
        _ingest_simulator_event(
            store,
            client=client,
            run_id=run.run_id,
            task_id=f"sim-deploy-{index}",
            kind=ControlPlaneEventKind.DEPLOYMENT_EVENT,
            status=ControlPlaneEventStatus.PASSED,
            summary=f"{display_name} emitted simulator fake deployment event.",
            evidence_digest=run.evidence_digest,
            role="release_guardian",
        )

    return store


def seed_multi_client_role_separation_store(db_path: Path, workspace_root: Path | None = None) -> ServerStore:
    store = seed_multi_client_simulator_store(db_path, workspace_root=workspace_root)
    evidence = RunEvidence(
        run_id="multi-client-role-separation",
        created_at="2026-07-03T00:10:00",
        eip=EvidenceRef(path="simulator/role-separation-eip.yaml", digest="role-separation-eip"),
        proof_plan=EvidenceRef(path="simulator/role-separation-proof-plan.yaml", digest="role-separation-proof"),
        agents=AgentEvidence(builder="codex-builder", verifier="vscode-verifier"),
        verification=VerificationEvidence(passed=False),
        effort=EffortEvidence(
            effort_type="scenario",
            mees=86,
            policy="needs_human",
            changed_files=0,
            line_delta=0,
            new_dependencies=0,
            complexity_delta=0,
            maintainability_delta=0,
        ),
        tokens=TokenEvidence(budget=12000, used=2200, remaining=9800),
        final_decision="needs_human",
    )
    run = store.save_run_evidence(evidence)

    _ingest_role_event(
        store,
        client_id="sim-antigravity",
        client_label="Antigravity",
        run_id=run.run_id,
        task_id="role-plan",
        kind=ControlPlaneEventKind.WORK_ITEM,
        status=ControlPlaneEventStatus.PASSED,
        actor="antigravity-planner",
        role="planner",
        summary="Planner produced separated role plan.",
        evidence_digest=run.evidence_digest,
    )
    lease_result = TaskLeaseService(store).acquire(TaskLeaseAcquireRequest(
        task_id="role-build",
        run_id=run.run_id,
        client_id="sim-codex",
        actor="codex-builder",
        role="builder",
        ttl_seconds=300,
    ))
    _ingest_role_event(
        store,
        client_id="sim-codex",
        client_label="Codex",
        run_id=run.run_id,
        task_id="role-build",
        kind=ControlPlaneEventKind.TOOL_CALL_STARTED,
        status=ControlPlaneEventStatus.ACTIVE,
        actor="codex-builder",
        role="builder",
        summary="Builder began separated role implementation.",
        evidence_digest=run.evidence_digest,
    )
    _ingest_role_event(
        store,
        client_id="sim-vscode",
        client_label="VS Code",
        run_id=run.run_id,
        task_id="role-proof",
        kind=ControlPlaneEventKind.PROOF_FINISHED,
        status=ControlPlaneEventStatus.PASSED,
        actor="vscode-verifier",
        role="verifier",
        summary="Verifier approved separated role proof.",
        evidence_digest=run.evidence_digest,
    )
    _ingest_role_event(
        store,
        client_id="sim-windsurf",
        client_label="Windsurf",
        run_id=run.run_id,
        task_id="role-audit",
        kind=ControlPlaneEventKind.WORK_ITEM,
        status=ControlPlaneEventStatus.PASSED,
        actor="windsurf-auditor",
        role="auditor",
        summary="Auditor reviewed role separation evidence.",
        evidence_digest=run.evidence_digest,
    )

    approval_service = ApprovalService(store)
    approval_service.record_approval(ApprovalRequest(
        run_id=run.run_id,
        actor="codex-builder",
        actor_role="release_guardian",
        action="approve_release",
        reason="Builder attempted to approve own work.",
    ))
    approval_service.record_approval(ApprovalRequest(
        run_id=run.run_id,
        actor="opencode-release-guardian",
        actor_role="release_guardian",
        action="approve_release",
        reason="Release guardian approved separated-role scenario after denial.",
    ))
    _ingest_role_event(
        store,
        client_id="sim-opencode",
        client_label="OpenCode",
        run_id=run.run_id,
        task_id="role-approval",
        kind=ControlPlaneEventKind.APPROVAL_RECORDED,
        status=ControlPlaneEventStatus.PASSED,
        actor="opencode-release-guardian",
        role="release_guardian",
        summary="Release guardian approved after self-approval denial.",
        evidence_digest=run.evidence_digest,
    )

    stale_snapshot = store.mark_client_stale("sim-codex")
    _ingest_role_event(
        store,
        client_id="sim-codex",
        client_label="Codex",
        run_id=run.run_id,
        task_id=lease_result.lease.task_id,
        kind=ControlPlaneEventKind.HEARTBEAT,
        status=ControlPlaneEventStatus.STALE,
        actor="sim-codex",
        role="trusted",
        summary=f"Builder client stale; lost task leases: {', '.join(stale_snapshot.lost_task_leases)}.",
        evidence_digest=run.evidence_digest,
    )
    _ingest_role_event(
        store,
        client_id="sim-generic-mcp-a2a",
        client_label="Generic MCP/A2A",
        run_id=run.run_id,
        task_id=lease_result.lease.task_id,
        kind=ControlPlaneEventKind.RECOVERY_ACTION,
        status=ControlPlaneEventStatus.NEEDS_HUMAN,
        actor="generic-mcp-deployer",
        role="operator",
        summary="Recovery path queued after stale builder lease expired.",
        evidence_digest=run.evidence_digest,
    )
    return store


def _approval_exists(store: ServerStore, run_id: str, approval_id: str) -> bool:
    return any(approval.approval_id == approval_id for approval in store.list_approval_events(run_id))


def _ingest_activity_event(store: ServerStore, activity: DashboardActivity, *, sequence: int) -> None:
    if _event_exists(store, activity.activity_id):
        return
    event = ControlPlaneEvent(
        event_id=activity.activity_id,
        sequence=sequence,
        created_at=activity.created_at,
        client_id=_safe_client_id(activity.client),
        client_label=activity.client,
        run_id=activity.run_id,
        task_id=activity.task_id,
        kind=ControlPlaneEventKind(activity.kind),
        status=ControlPlaneEventStatus(activity.status),
        actor=activity.actor,
        role=activity.role,
        evidence_digest=activity.evidence_digest,
        summary=activity.summary,
    )
    ControlPlaneEventService(store).ingest(event.model_dump(mode="json"))


def _event_exists(store: ServerStore, event_id: str) -> bool:
    return any(event.event_id == event_id for event in store.list_control_plane_events())


def _ingest_simulator_event(
    store: ServerStore,
    *,
    client: dict[str, object],
    run_id: str,
    task_id: str,
    kind: ControlPlaneEventKind,
    status: ControlPlaneEventStatus,
    summary: str,
    evidence_digest: str,
    role: str | None = None,
) -> None:
    event_id = f"multi-client-simulator-{client['client_id']}-{kind.value}-{task_id}"
    if _event_exists(store, event_id):
        return
    event = ControlPlaneEvent(
        event_id=event_id,
        sequence=len(store.list_control_plane_events()) + 1,
        client_id=str(client["client_id"]),
        client_label=str(client["display_name"]),
        run_id=run_id,
        task_id=task_id,
        kind=kind,
        status=status,
        actor=str(client["actor"]),
        role=role or str(client["role"]),
        evidence_digest=evidence_digest,
        summary=summary,
    )
    ControlPlaneEventService(store).ingest(event.model_dump(mode="json"))


def _ingest_role_event(
    store: ServerStore,
    *,
    client_id: str,
    client_label: str,
    run_id: str,
    task_id: str,
    kind: ControlPlaneEventKind,
    status: ControlPlaneEventStatus,
    actor: str,
    role: str,
    summary: str,
    evidence_digest: str,
    event_role: str | None = None,
) -> None:
    event_id = f"{run_id}-{client_id}-{kind.value}-{task_id}"
    if _event_exists(store, event_id):
        return
    event = ControlPlaneEvent(
        event_id=event_id,
        sequence=len(store.list_control_plane_events()) + 1,
        client_id=client_id,
        client_label=client_label,
        run_id=run_id,
        task_id=task_id,
        kind=kind,
        status=status,
        actor=actor,
        role=event_role or role,
        evidence_digest=evidence_digest,
        summary=summary,
    )
    ControlPlaneEventService(store).ingest(event.model_dump(mode="json"))


def _safe_client_id(client: str) -> str:
    return "".join(character if character.isalnum() or character in {"-", "_", ".", " "} else "-" for character in client)
