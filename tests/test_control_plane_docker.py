from pathlib import Path
import json
import threading
from urllib.request import urlopen

from vad.control_plane.config import ControlPlaneConfig
from vad.control_plane.server import prepare_control_plane_server
from vad.server.app import VADApi


def test_compose_file_defines_level4_control_plane_service():
    text = Path("compose.yaml").read_text(encoding="utf-8")

    assert "vad-control-plane:" in text
    assert "profiles:" in text
    assert "- level4" in text
    assert "local-os" in text
    assert "demo" in text
    assert "--allow-non-local-bind" in text
    assert "8081:8080" in text
    assert "VAD_LIVE_SERVICES: \"disabled\"" in text
    assert "/tmp/vad-local-os/plugins" in text
    level4_service = text.split("  vad-control-plane:", 1)[1]
    assert "--seed-level3-demo" not in level4_service


def test_plugin_status_api_reports_seeded_local_status(tmp_path):
    status, payload = VADApi(tmp_path).handle("GET", "/plugins/status")

    assert status == 200
    assert payload["status"] == "seeded"
    assert payload["status_counts"] == {
        "available": 1,
        "failed": 1,
        "installed": 1,
        "needs_review": 1,
    }


def test_control_plane_docs_record_compose_smoke_routes():
    text = Path("docs/control-plane.md").read_text(encoding="utf-8")

    for route in [
        "http://localhost:8081/health",
        "http://localhost:8081/ready",
        "http://localhost:8081/",
        "http://localhost:8081/dashboard",
        "http://localhost:8081/dashboard/replay?run_id=multi-client-simulator",
        "http://localhost:8081/plugins/status",
    ]:
        assert route in text
    assert "deterministic plugin status seed" in text
    assert "plugin inventory persistence" in text


def test_control_plane_server_smoke_hits_level4_routes(tmp_path):
    server = prepare_control_plane_server(
        ControlPlaneConfig(
            port=0,
            db_path=tmp_path / "vad.sqlite3",
            ui_root=tmp_path / "ui",
            evidence_root=tmp_path / "evidence",
            plugin_root=tmp_path / "plugins",
        ),
        seed_level3_demo=True,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address[:2]
        base_url = f"http://{host}:{port}"
        with urlopen(f"{base_url}/health") as response:
            assert response.status == 200
        with urlopen(f"{base_url}/ready") as response:
            assert json.loads(response.read()) == {"status": "ready"}
        with urlopen(f"{base_url}/") as response:
            assert 'id="dashboard-panel"' in response.read().decode("utf-8")
        with urlopen(f"{base_url}/dashboard") as response:
            dashboard = json.loads(response.read())
            assert "Codex" in dashboard["client_counts"]
        with urlopen(f"{base_url}/plugins/status") as response:
            plugin_status = json.loads(response.read())
            assert plugin_status["status"] == "seeded"
            assert {plugin["status"] for plugin in plugin_status["plugins"]} == {
                "installed",
                "available",
                "failed",
                "needs_review",
            }
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
