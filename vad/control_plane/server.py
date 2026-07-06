from __future__ import annotations

from dataclasses import dataclass

from vad.control_plane.config import ControlPlaneConfig
from vad.control_plane.lifecycle import ControlPlaneLifecycle
from vad.server.serve import prepare_ui_server


@dataclass
class ControlPlaneRunResult:
    lifecycle: ControlPlaneLifecycle
    bound_host: str
    bound_port: int
    warning: str | None = None


def prepare_control_plane_server(
    config: ControlPlaneConfig,
    *,
    seed_demo: bool = False,
    seed_level3_demo: bool = False,
    seed_multi_client_simulator: bool = False,
):
    return prepare_ui_server(
        config.bind_host,
        config.port,
        config.evidence_root,
        config.db_path,
        config.ui_root,
        seed_demo=seed_demo,
        seed_level3_demo=seed_level3_demo,
        seed_multi_client_simulator=seed_multi_client_simulator,
    )


def run_control_plane(
    config: ControlPlaneConfig,
    *,
    seed_demo: bool = False,
    seed_level3_demo: bool = False,
    seed_multi_client_simulator: bool = False,
    serve_forever: bool = True,
) -> ControlPlaneRunResult:
    lifecycle = ControlPlaneLifecycle.start()
    server = prepare_control_plane_server(
        config,
        seed_demo=seed_demo,
        seed_level3_demo=seed_level3_demo,
        seed_multi_client_simulator=seed_multi_client_simulator,
    )
    bound_host, bound_port = server.server_address[:2]
    lifecycle.mark_ready()
    warning = config.non_local_bind_warning()
    if warning:
        print(f"WARNING: {warning}", flush=True)
    print(f"Serving VAD control plane on http://{bound_host}:{bound_port}", flush=True)
    try:
        if serve_forever:
            server.serve_forever()
        else:
            lifecycle.request_shutdown(reason="test start completed", actor="test")
    finally:
        server.server_close()
        if lifecycle.state.value == "draining":
            lifecycle.stop()
    return ControlPlaneRunResult(lifecycle=lifecycle, bound_host=bound_host, bound_port=bound_port, warning=warning)
