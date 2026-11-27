"""
------------
- clean.py -
------------

A probe for test_conftest.py, never collected on its own: a module that leaks nothing, run after
leaks.py, so a leak that waited for a later collection would fail here instead.
"""


def test_nothing_is_left_open() -> None:
    """(It does nothing, so it fails only for what an earlier module left.)"""
