from __future__ import annotations

import argparse
from pathlib import Path

from vad.server.app import create_server
from vad.server.fixtures import seed_demo_store, seed_level3_demo_store, seed_multi_client_simulator_store
from vad.ui.build import build_ui


def prepare_ui_server(
    host: str,
    port: int,
    evidence_root: Path,
    db_path: Path,
    ui_root: Path,
    *,
    seed_demo: bool = False,
    seed_level3_demo: bool = False,
    seed_multi_client_simulator: bool = False,
):
    build_ui(ui_root)
    if seed_multi_client_simulator:
        seed_multi_client_simulator_store(db_path)
    elif seed_level3_demo:
        seed_level3_demo_store(db_path)
    elif seed_demo:
        seed_demo_store(db_path)
    else:
        db_path.parent.mkdir(parents=True, exist_ok=True)

    return create_server(
        host,
        port,
        evidence_root,
        db_path=db_path,
        ui_root=ui_root,
    )


def serve_ui(
    host: str,
    port: int,
    evidence_root: Path,
    db_path: Path,
    ui_root: Path,
    *,
    seed_demo: bool = False,
    seed_level3_demo: bool = False,
    seed_multi_client_simulator: bool = False,
) -> None:
    server = prepare_ui_server(
        host,
        port,
        evidence_root,
        db_path,
        ui_root,
        seed_demo=seed_demo,
        seed_level3_demo=seed_level3_demo,
        seed_multi_client_simulator=seed_multi_client_simulator,
    )
    bound_host, bound_port = server.server_address[:2]
    print(f"Serving VAD UI/API on http://{bound_host}:{bound_port}", flush=True)
    server.serve_forever()


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m vad.server.serve")
    parser.add_argument("--host", default="0.0.0.0", help="Host interface to bind")
    parser.add_argument("--port", type=int, default=8080, help="Port to bind")
    parser.add_argument("--evidence-root", default="/tmp/vad-evidence", help="Evidence file directory")
    parser.add_argument("--db", default="/tmp/vad-server/vad.sqlite3", help="SQLite database path")
    parser.add_argument("--ui-root", default="/tmp/vad-ui", help="Built UI directory")
    parser.add_argument("--seed-demo", action="store_true", help="Seed deterministic local demo data")
    parser.add_argument("--seed-level3-demo", action="store_true", help="Seed deterministic Level 3 demonstrator data")
    parser.add_argument("--seed-multi-client-simulator", action="store_true", help="Seed deterministic Level 4 multi-client simulator data")
    args = parser.parse_args()

    serve_ui(
        args.host,
        args.port,
        Path(args.evidence_root),
        Path(args.db),
        Path(args.ui_root),
        seed_demo=args.seed_demo,
        seed_level3_demo=args.seed_level3_demo,
        seed_multi_client_simulator=args.seed_multi_client_simulator,
    )


if __name__ == "__main__":
    main()
