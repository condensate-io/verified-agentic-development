import pytest

from vad.loop.state import LoopState, LoopStatus


def test_loop_state_valid_transitions():
    state = LoopState()
    state = state.transition(LoopStatus.ASSESSED)
    state = state.transition(LoopStatus.PLANNED)
    state = state.transition(LoopStatus.POLICY_CHECKED)
    state = state.transition(LoopStatus.VERIFYING)
    state = state.transition(LoopStatus.PASSED)

    assert state.status == LoopStatus.PASSED
    assert [item.value for item in state.history] == [
        "initialized",
        "assessed",
        "planned",
        "policy_checked",
        "verifying",
        "passed",
    ]


def test_loop_state_invalid_transition_fails():
    state = LoopState()

    with pytest.raises(ValueError, match="Invalid loop transition"):
        state.transition(LoopStatus.PASSED)
