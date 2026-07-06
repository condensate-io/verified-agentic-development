import json
import sys

import pytest

DIGEST = "a" * 64


def run_cli(monkeypatch, argv, capsys):
    from vad import cli

    monkeypatch.setattr(sys, "argv", argv)
    cli.main()
    output = capsys.readouterr().out
    json_start = output.find("{")
    return json.JSONDecoder().raw_decode(output[json_start:])[0] if json_start >= 0 else None


def write_manifest(path):
    path.write_text(json.dumps({
        "plugin_id": "vad-codex-local",
        "target_client": "codex",
        "version": "1.0.0",
        "command": {
            "executable": "python",
            "args": ["-m", "vad.adapters.mcp"],
        },
        "config_paths": [
            {"scope": "workspace", "path": ".codex/vad-mcp.json"},
        ],
        "permissions": [
            {"name": "mcp_stdio", "reason": "Expose the local VAD MCP server."},
        ],
        "tools": [
            {"name": "repo_assess", "role": "observer"},
        ],
        "prompts": [
            {"prompt_id": "vad-builder", "role": "builder", "path": "prompts/builder.md"},
        ],
        "checksums": {
            "plugin.json": DIGEST,
            "prompts/builder.md": "b" * 64,
        },
    }), encoding="utf-8")


def test_cli_plugins_install_dry_run_prints_plan_without_writes(monkeypatch, capsys, tmp_path):
    manifest = tmp_path / "plugin.json"
    workspace_root = tmp_path / "workspace"
    user_config_root = tmp_path / "user-config"
    write_manifest(manifest)

    payload = run_cli(monkeypatch, [
        "vad",
        "plugins",
        "install",
        str(manifest),
        "--dry-run",
        "--workspace-root",
        str(workspace_root),
        "--user-config-root",
        str(user_config_root),
    ], capsys)

    assert payload["dry_run"] is True
    assert payload["writes_performed"] == 0
    assert payload["operations"][0]["path"] == str((workspace_root / ".codex" / "vad-mcp.json").resolve())
    assert payload["operations"][0]["change"] == "write vad-codex-local config for codex"
    assert payload["rollback"][0]["action"] == "restore_or_remove"
    assert payload["artifact"]["artifact_digest"]
    assert not workspace_root.exists()
    assert not user_config_root.exists()


def test_cli_plugins_install_dry_run_out_writes_plan_only(monkeypatch, capsys, tmp_path):
    manifest = tmp_path / "plugin.json"
    output = tmp_path / "dry-run.json"
    workspace_root = tmp_path / "workspace"
    write_manifest(manifest)

    run_cli(monkeypatch, [
        "vad",
        "plugins",
        "install",
        str(manifest),
        "--dry-run",
        "--workspace-root",
        str(workspace_root),
        "--out",
        str(output),
    ], capsys)

    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["dry_run"] is True
    assert payload["writes_performed"] == 0
    assert payload["operations"][0]["path"] == str((workspace_root / ".codex" / "vad-mcp.json").resolve())
    assert not workspace_root.exists()


def test_cli_plugins_install_requires_dry_run(monkeypatch, capsys, tmp_path):
    manifest = tmp_path / "plugin.json"
    write_manifest(manifest)

    with pytest.raises(SystemExit) as exc:
        run_cli(monkeypatch, ["vad", "plugins", "install", str(manifest)], capsys)

    assert exc.value.code == 1
    assert "dry-run only" in capsys.readouterr().err


def test_cli_plugins_install_rejects_invalid_manifest(monkeypatch, capsys, tmp_path):
    manifest = tmp_path / "plugin.json"
    manifest.write_text(json.dumps({"plugin_id": "../escape"}), encoding="utf-8")

    with pytest.raises(SystemExit) as exc:
        run_cli(monkeypatch, ["vad", "plugins", "install", str(manifest), "--dry-run"], capsys)

    assert exc.value.code == 1
    assert "Plugin install dry-run failed" in capsys.readouterr().err
