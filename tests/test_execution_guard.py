from vad.guards.execution import ExecutionGuard


def test_execution_guard_allows_pytest_command():
    guard = ExecutionGuard()

    result = guard.run(["pytest", "--version"])

    assert result.allowed is True
    assert result.exit_code == 0
    assert "pytest" in result.stdout


def test_execution_guard_denies_disallowed_executable_before_run():
    guard = ExecutionGuard()

    result = guard.run(["rm", "-rf", "important"])

    assert result.allowed is False
    assert result.exit_code is None
    assert "executable not allowed" in result.stderr


def test_execution_guard_denies_shell_control_syntax():
    guard = ExecutionGuard()

    result = guard.run(["pytest", "tests", "&&", "echo", "bad"])

    assert result.allowed is False
    assert result.exit_code is None
    assert "shell control" in result.stderr
