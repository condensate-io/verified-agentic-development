import sys
from pathlib import Path

from vad.control_plane.config import ControlPlaneConfig
from vad.control_plane.lifecycle import ControlPlaneState
from vad.control_plane.server import run_control_plane
from vad.server.app import VADApi


class FakeServer:
    def __init__(self):
        self.server_address = ("127.0.0.1", 0)
        self.closed = False
        self.served = False

    def serve_forever(self):
        self.served = True

    def server_close(self):
        self.closed = True


def test_ready_endpoint_reports_ready(tmp_path):
    status, payload = VADApi(tmp_path).handle("GET", "/ready")

    assert status == 200
    assert payload == {"status": "ready"}


def test_run_control_plane_test_start_reaches_ready_then_stops(monkeypatch, tmp_path):
    import vad.control_plane.server as server_module

    fake_server = FakeServer()
    captured = {}

    def fake_prepare(config, *, seed_demo=False, seed_level3_demo=False, seed_multi_client_simulator=False):
        captured["config"] = config
        captured["seed_demo"] = seed_demo
        captured["seed_level3_demo"] = seed_level3_demo
        captured["seed_multi_client_simulator"] = seed_multi_client_simulator
        return fake_server

    monkeypatch.setattr(server_module, "prepare_control_plane_server", fake_prepare)
    config = ControlPlaneConfig(port=9090, db_path=tmp_path / "vad.sqlite3")

    result = run_control_plane(config, seed_demo=True, serve_forever=False)

    assert captured["config"].port == 9090
    assert captured["seed_demo"] is True
    assert captured["seed_level3_demo"] is False
    assert captured["seed_multi_client_simulator"] is False
    assert fake_server.closed is True
    assert fake_server.served is False
    assert result.lifecycle.state == ControlPlaneState.STOPPED
    assert [event.to_state for event in result.lifecycle.events] == [
        ControlPlaneState.STARTING,
        ControlPlaneState.READY,
        ControlPlaneState.DRAINING,
        ControlPlaneState.STOPPED,
    ]


def test_control_plane_serve_cli_uses_control_plane_defaults(monkeypatch, tmp_path):
    from vad import cli
    import vad.control_plane.server as server_module

    captured = {}

    def fake_run_control_plane(config, *, seed_demo=False, seed_level3_demo=False, seed_multi_client_simulator=False, serve_forever=True):
        captured["config"] = config
        captured["seed_demo"] = seed_demo
        captured["seed_level3_demo"] = seed_level3_demo
        captured["seed_multi_client_simulator"] = seed_multi_client_simulator
        captured["serve_forever"] = serve_forever

    monkeypatch.setattr(server_module, "run_control_plane", fake_run_control_plane)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "vad",
            "control-plane",
            "serve",
            "--test-start",
        ],
    )

    cli.main()

    config = captured["config"]
    assert config.bind_host == "127.0.0.1"
    assert config.db_path == Path(".vad/control-plane/vad.sqlite3")
    assert config.evidence_root == Path(".vad/control-plane/evidence")
    assert config.ui_root == Path(".vad/control-plane/ui")
    assert config.plugin_root == Path(".vad/control-plane/plugins")
    assert captured["seed_multi_client_simulator"] is False
    assert captured["serve_forever"] is False


def test_control_plane_serve_cli_accepts_explicit_non_local_bind(monkeypatch):
    from vad import cli
    import vad.control_plane.server as server_module

    captured = {}

    def fake_run_control_plane(config, *, seed_demo=False, seed_level3_demo=False, seed_multi_client_simulator=False, serve_forever=True):
        captured["config"] = config

    monkeypatch.setattr(server_module, "run_control_plane", fake_run_control_plane)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "vad",
            "control-plane",
            "serve",
            "--host",
            "0.0.0.0",
            "--allow-non-local-bind",
            "--test-start",
        ],
    )

    cli.main()

    assert captured["config"].bind_host == "0.0.0.0"
    assert captured["config"].allow_non_local_bind is True


def test_local_os_demo_cli_starts_local_simulator_seed(monkeypatch, tmp_path):
    from vad import cli
    import vad.control_plane.server as server_module

    captured = {}

    def fake_run_control_plane(config, *, seed_demo=False, seed_level3_demo=False, seed_multi_client_simulator=False, serve_forever=True):
        captured["config"] = config
        captured["seed_demo"] = seed_demo
        captured["seed_level3_demo"] = seed_level3_demo
        captured["seed_multi_client_simulator"] = seed_multi_client_simulator
        captured["serve_forever"] = serve_forever

    monkeypatch.setattr(server_module, "run_control_plane", fake_run_control_plane)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "vad",
            "local-os",
            "demo",
            "--port",
            "0",
            "--db",
            str(tmp_path / "vad.sqlite3"),
            "--evidence-root",
            str(tmp_path / "evidence"),
            "--ui-root",
            str(tmp_path / "ui"),
            "--plugin-root",
            str(tmp_path / "plugins"),
            "--allow-non-local-bind",
            "--test-start",
        ],
    )

    cli.main()

    config = captured["config"]
    assert config.bind_host == "127.0.0.1"
    assert config.port == 0
    assert config.db_path == tmp_path / "vad.sqlite3"
    assert config.evidence_root == tmp_path / "evidence"
    assert config.ui_root == tmp_path / "ui"
    assert config.plugin_root == tmp_path / "plugins"
    assert config.allow_non_local_bind is True
    assert captured["seed_demo"] is False
    assert captured["seed_level3_demo"] is False
    assert captured["seed_multi_client_simulator"] is True
    assert captured["serve_forever"] is False
