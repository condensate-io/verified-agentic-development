import json
from pathlib import Path
from unittest.mock import patch
import pytest

from vad.cli import main


def test_cli_mcp_work_intake_print_config_claude(capsys):
    with patch('sys.argv', ['vad', 'mcp', 'work-intake', '--print-config', 'claude']):
        main()
    captured = capsys.readouterr()
    assert 'vad-work-intake' in captured.out
    cfg = json.loads(captured.out)
    assert 'mcpServers' in cfg
    assert 'vad-work-intake' in cfg['mcpServers']
    assert 'mcp' in cfg['mcpServers']['vad-work-intake']['args']
    assert 'work-intake' in cfg['mcpServers']['vad-work-intake']['args']


def test_cli_mcp_work_intake_print_config_cursor(capsys):
    with patch('sys.argv', ['vad', 'mcp', 'work-intake', '--print-config', 'cursor']):
        main()
    captured = capsys.readouterr()
    assert 'vad-work-intake' in captured.out
    cfg = json.loads(captured.out)
    assert 'vad-work-intake' in cfg
    assert cfg['vad-work-intake']['type'] == 'stdio'


def test_cli_mcp_work_intake_install_claude(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    with patch('sys.argv', ['vad', 'mcp', 'work-intake', '--install', 'claude']):
        main()
    captured = capsys.readouterr()
    assert '[SUCCESS]' in captured.out
    cfg_file = tmp_path / '.claudecode' / 'config.json'
    assert cfg_file.exists()
    content = json.loads(cfg_file.read_text())
    assert 'vad-work-intake' in content['mcpServers']


def test_cli_mcp_work_intake_test_start(tmp_path, capsys):
    test_db = str(tmp_path / 'test.sqlite3')
    with patch('sys.argv', ['vad', 'mcp', 'work-intake', '--test-start', '--db', test_db]):
        main()
    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert data['status'] == 'ready'
    assert data['command'] == 'work-intake'
    assert str(Path(test_db).resolve()) == data['db']
