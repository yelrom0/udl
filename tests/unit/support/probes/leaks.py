"""
------------
- leaks.py -
------------

A probe for test_conftest.py, never collected on its own (its name isn't `test_*`): a test that
leaves an open socket in a reference cycle, the way a test leaks one when it drops an object that
holds a connection without closing it.

Automatic collection is off, as if this worker were too busy to run it before the module ends:
only a collection that tests/conftest.py makes finds the socket, so the leak fails here, in the
module that made it, or later in another one.
"""

# System Imports
import gc
import socket

gc.disable()


def test_a_socket_is_left_open() -> None:
    """(The list holds itself and the socket, so only a collection frees them.)"""
    held: list[object] = [socket.socket()]
    held.append(held)
