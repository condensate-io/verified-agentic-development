from pathlib import Path

import pytest
from pydantic import ValidationError

from vad.control_plane.config import ControlPlaneConfig, parse_bool


def test_control_plane_config_defaults_are_local_and_under_vad_directory():
    config = ControlPlaneConfig()

    assert config.bind_host == "127.0.0.1"
    assert config.port == 8080
    assert config.config_path == Path(".vad/control-plane/config.json")
    assert config.db_path == Path(".vad/control-plane/vad.sqlite3")
    assert config.evidence_root == Path(".vad/control-plane/evidence")
    assert config.ui_root == Path(".vad/control-plane/ui")
    assert config.plugin_root == Path(".vad/control-plane/plugins")
    assert config.log_level == "info"
    assert config.non_local_bind_warning() is None


def test_control_plane_config_env_overrides_are_typed():
    config = ControlPlaneConfig.from_env({
        "VAD_CONTROL_PLANE_CONFIG": "local/config.json",
        "VAD_CONTROL_PLANE_HOST": "localhost",
        "VAD_CONTROL_PLANE_PORT": "9090",
        "VAD_CONTROL_PLANE_DB": "local/vad.sqlite3",
        "VAD_CONTROL_PLANE_EVIDENCE_ROOT": "local/evidence",
        "VAD_CONTROL_PLANE_UI_ROOT": "local/ui",
        "VAD_CONTROL_PLANE_PLUGIN_ROOT": "local/plugins",
        "VAD_CONTROL_PLANE_LOG_LEVEL": "DEBUG",
    })

    assert config.config_path == Path("local/config.json")
    assert config.bind_host == "localhost"
    assert config.port == 9090
    assert config.db_path == Path("local/vad.sqlite3")
    assert config.evidence_root == Path("local/evidence")
    assert config.ui_root == Path("local/ui")
    assert config.plugin_root == Path("local/plugins")
    assert config.log_level == "debug"


def test_non_local_bind_requires_explicit_opt_in():
    with pytest.raises(ValidationError, match="non-local control-plane bind"):
        ControlPlaneConfig(bind_host="0.0.0.0")

    config = ControlPlaneConfig(bind_host="0.0.0.0", allow_non_local_bind=True)

    assert config.non_local_bind_warning() == "control plane will bind to non-local host 0.0.0.0; use only on trusted local networks"


def test_control_plane_config_rejects_secret_markers():
    with pytest.raises(ValidationError, match="must not contain secret values"):
        ControlPlaneConfig(db_path=Path(".vad/control-plane/token=sk-123.sqlite3"))

    with pytest.raises(ValidationError, match="must not contain secret values"):
        ControlPlaneConfig.from_env({"VAD_CONTROL_PLANE_HOST": "password=abc"})


def test_parse_bool_accepts_explicit_values_and_rejects_ambiguous_values():
    assert parse_bool("true") is True
    assert parse_bool("0") is False
    with pytest.raises(ValueError, match="invalid boolean value"):
        parse_bool("maybe")
