from vad.repo.proof_discovery import discover_proof_commands


def test_python_pytest_fixture_discovered(tmp_path):
    (tmp_path / "tests").mkdir()

    discovery = discover_proof_commands(tmp_path)

    assert discovery.commands[0].ecosystem == "python"
    assert discovery.commands[0].command == ["pytest"]
    assert discovery.blocker is None


def test_node_package_test_script_discovered(tmp_path):
    (tmp_path / "package.json").write_text(
        '{"scripts": {"test": "node --test"}}',
        encoding="utf-8",
    )

    discovery = discover_proof_commands(tmp_path)

    assert discovery.commands[0].ecosystem == "node"
    assert discovery.commands[0].command == ["npm", "test"]


def test_go_test_fixture_discovered(tmp_path):
    (tmp_path / "go.mod").write_text("module example.com/test\n", encoding="utf-8")

    discovery = discover_proof_commands(tmp_path)

    assert discovery.commands[0].ecosystem == "go"
    assert discovery.commands[0].command == ["go", "test", "./..."]


def test_no_proof_command_blocks_autonomous_completion(tmp_path):
    discovery = discover_proof_commands(tmp_path)

    assert discovery.has_proof_commands is False
    assert discovery.blocker == "no proof command discovered"


def test_invalid_package_json_does_not_fake_node_proof(tmp_path):
    (tmp_path / "package.json").write_text("{bad json", encoding="utf-8")

    discovery = discover_proof_commands(tmp_path)

    assert discovery.commands == []
    assert discovery.blocker == "no proof command discovered"
