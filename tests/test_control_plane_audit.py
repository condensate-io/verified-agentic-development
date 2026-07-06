import sys
from pathlib import Path


def test_control_plane_audit_documents_existing_boundary_and_delta():
    text = Path("docs/control-plane.md").read_text(encoding="utf-8")

    for required in [
        "Implemented Local Level 4 Boundary",
        "vad ui serve",
        "vad control-plane serve",
        "vad local-os demo",
        "vad.server.serve",
        "vad.server.app",
        "vad.server.db.store",
        "compose.yaml",
        "vad.adapters.mcp",
        "Reference Architecture Delta Closure",
        "dynamic MCP gateway",
        "client registry",
        "append-only event ledger",
        "Remaining Local Hardening",
        "Future Cloud Scope",
    ]:
        assert required in text


def test_control_plane_audit_records_local_only_binding_and_cloud_boundary():
    text = Path("docs/control-plane.md").read_text(encoding="utf-8")

    assert "vad ui serve --host defaults to 127.0.0.1" in text
    assert "lower-level module currently defaults to `0.0.0.0`" in text
    assert "must not be treated as the future control-plane default" in text
    assert "no hosted VAD SaaS" in text
    assert "no cloud dashboard" in text
    assert "bind to localhost by default" in text


def test_ui_serve_cli_defaults_to_localhost(monkeypatch, tmp_path):
    from vad import cli
    import vad.server.serve as serve_module

    captured = {}

    class FakeServer:
        server_address = ("127.0.0.1", 0)

        def serve_forever(self):
            captured["served"] = True

    def fake_prepare_ui_server(
        host,
        port,
        evidence_root,
        db_path,
        ui_root,
        *,
        seed_demo=False,
        seed_level3_demo=False,
        seed_multi_client_simulator=False,
    ):
        captured["host"] = host
        captured["port"] = port
        captured["evidence_root"] = evidence_root
        captured["db_path"] = db_path
        captured["ui_root"] = ui_root
        captured["seed_multi_client_simulator"] = seed_multi_client_simulator
        return FakeServer()

    monkeypatch.setattr(serve_module, "prepare_ui_server", fake_prepare_ui_server)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "vad",
            "ui",
            "serve",
            "--port",
            "0",
            "--evidence-root",
            str(tmp_path / "evidence"),
            "--db",
            str(tmp_path / "vad.sqlite3"),
            "--ui-root",
            str(tmp_path / "ui"),
        ],
    )

    cli.main()

    assert captured["host"] == "127.0.0.1"
    assert captured["port"] == 0
    assert captured["seed_multi_client_simulator"] is False
    assert captured["served"] is True
