from examples.app.main import add, divide, buggy_add
from hypothesis import given
from hypothesis.strategies import integers
import pytest

def test_add():
    assert add(1, 2) == 3

@given(integers(), integers())
def test_add_properties(a, b):
    assert add(a, b) == a + b

@given(integers(), integers())
def test_buggy_add_properties(a, b):
    assert buggy_add(a, b) == a + b
