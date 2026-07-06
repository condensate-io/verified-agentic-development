from pathlib import Path


DOCS = Path(__file__).resolve().parents[1] / "docs"

GUIDES = [
    "repo-automation.md",
    "signing.md",
    "providers.md",
    "swarm.md",
    "deployment.md",
    "ui.md",
    "level4-operator.md",
]


def test_operator_guides_exist_and_mark_command_coverage():
    for guide in GUIDES:
        text = (DOCS / guide).read_text(encoding="utf-8")
        assert "## Command Coverage" in text, guide
        assert "Tested:" in text, guide
        assert "Illustrative:" in text or "Opt-in only:" in text or "Not implemented:" in text, guide


def test_repo_operator_guide_marks_patch_inputs_explicit():
    text = (DOCS / "repo-automation.md").read_text(encoding="utf-8")

    assert "does not synthesize patches by itself" in text
    assert "blocks dependency manifest changes" in text
    assert "rolls back the patch when proof fails" in text


def test_signing_and_swarm_guides_do_not_claim_production_control_planes():
    signing = (DOCS / "signing.md").read_text(encoding="utf-8")
    swarm = (DOCS / "swarm.md").read_text(encoding="utf-8")

    assert "not production key management" in signing
    assert "not a production distributed agent fleet" in swarm
    assert "copies the fixture repository before making changes" in swarm


def test_ui_and_deployment_guides_reference_level3_local_boundaries():
    ui = (DOCS / "ui.md").read_text(encoding="utf-8")
    deployment = (DOCS / "deployment.md").read_text(encoding="utf-8")

    assert "fake and local-only" in ui
    assert "They are not live deployment commands" in deployment
    assert "docker compose up vad-ui" in ui


def test_level4_operator_guide_covers_start_stop_connect_dashboard_and_recovery():
    text = (DOCS / "level4-operator.md").read_text(encoding="utf-8")

    for expected in [
        "vad local-os demo",
        "vad control-plane serve",
        "docker compose --profile level4 up -d --build vad-control-plane",
        "docker compose --profile level4 down",
        "GET http://127.0.0.1:8080/health",
        "GET http://127.0.0.1:8080/ready",
        "vad mcp run",
        "vad clients register",
        "vad clients heartbeat",
        "vad clients mark-stale",
        "/dashboard",
        "/dashboard/replay?run_id=multi-client-simulator",
        "active_clients",
        "event_timeline",
        "task_board_columns",
        "task_leases",
        "proof_status",
        "terminal_status",
        "plugin_status",
        "POST http://127.0.0.1:8080/clients/stale-scan",
        "/leases/{task_id}/expire",
        "/leases/{task_id}/approval-check",
        "release_guardian",
        "Builder self-approval is denied",
        "lost_task_leases",
    ]:
        assert expected in text


def test_level4_operator_guide_keeps_local_only_boundary():
    text = (DOCS / "level4-operator.md").read_text(encoding="utf-8")

    for expected in [
        "127.0.0.1",
        "VAD_LIVE_SERVICES=disabled",
        "not a hosted VAD SaaS",
        "not as a remote service",
        "Do not add live credentials",
        "not as a remote service",
        "Not implemented: hosted VAD SaaS",
        "remote MCP gateway",
        "live production providers",
        "marketplace publication",
        "production key management",
    ]:
        assert expected in text

    forbidden_claims = [
        "managed tenant is implemented",
        "cloud dashboard is implemented",
        "remote MCP gateway is implemented",
        "marketplace publication is implemented",
        "production key management is implemented",
    ]
    for claim in forbidden_claims:
        assert claim not in text
