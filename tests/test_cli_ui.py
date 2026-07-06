import sys
from pathlib import Path


class FakeServer:
    def __init__(self):
        self.server_address = ("127.0.0.1", 0)
        self.served = False

    def serve_forever(self):
        self.served = True


def test_ui_serve_command_uses_local_server_entrypoint(monkeypatch, tmp_path):
    from vad import cli
    import vad.server.serve as serve_module

    captured = {}
    fake_server = FakeServer()

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
        captured["seed_demo"] = seed_demo
        captured["seed_level3_demo"] = seed_level3_demo
        captured["seed_multi_client_simulator"] = seed_multi_client_simulator
        return fake_server

    monkeypatch.setattr(serve_module, "prepare_ui_server", fake_prepare_ui_server)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "vad",
            "ui",
            "serve",
            "--host",
            "127.0.0.1",
            "--port",
            "0",
            "--evidence-root",
            str(tmp_path / "evidence"),
            "--db",
            str(tmp_path / "vad.sqlite3"),
            "--ui-root",
            str(tmp_path / "ui"),
            "--seed-demo",
        ],
    )

    cli.main()

    assert captured == {
        "host": "127.0.0.1",
        "port": 0,
        "evidence_root": Path(tmp_path / "evidence"),
        "db_path": Path(tmp_path / "vad.sqlite3"),
        "ui_root": Path(tmp_path / "ui"),
        "seed_demo": True,
        "seed_level3_demo": False,
        "seed_multi_client_simulator": False,
    }
    assert fake_server.served is True
