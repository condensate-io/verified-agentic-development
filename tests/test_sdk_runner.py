import pytest
from vad.runners.base_adapter import RunnerTaskSpec
from vad.runners.sdk_runner import RunnerRegistry, SDKRunner


def test_sdk_runner_registry_initialization():
    registry = RunnerRegistry.default_registry()
    runners = registry.list_all()
    assert "claude" in runners
    assert "copilot" in runners
    assert "cursor" in runners
    assert "codex" in runners
    assert "aider" in runners


def test_sdk_runner_execution_selection():
    runner = SDKRunner()
    spec = RunnerTaskSpec(
        task_id="T-SDK-1",
        prompt="Write contract tests for payment gateway",
    )

    # Dispatch to Claude
    res_claude = runner.execute(spec, runner_name="claude")
    assert res_claude.success is True
    assert res_claude.runner_name == "claude"

    # Dispatch to Aider
    res_aider = runner.execute(spec, runner_name="aider")
    assert res_aider.success is True
    assert res_aider.runner_name == "aider"

    # Dispatch to Cursor
    res_cursor = runner.execute(spec, runner_name="cursor")
    assert res_cursor.success is True
    assert res_cursor.runner_name == "cursor"


def test_sdk_runner_applies_tool_policy():
    runner = SDKRunner()
    spec = RunnerTaskSpec(
        task_id="T-SDK-TOOLS",
        prompt="Execute terminal diagnostics",
    )

    res = runner.execute(spec, runner_name="claude", tool_policy=["pytest"])
    assert res.success is True
    assert res.tools_invoked == ["pytest"]
