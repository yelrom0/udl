"""
------------
- hosts.py -
------------

udl hosts: the machines agents run on. The group's commands (list, set, rm, check) are made
from their operations (udl.interfaces.adapters.add_commands), shown by these renderers.
"""

# System Imports
from collections.abc import Callable
from typing import Final

# Local Imports
from udl.interfaces.cli import render

RENDERERS: Final[dict[str, Callable[..., None]]] = {
    "list_hosts": render.hosts,
    "set_host": render.host,
    "delete_host": render.deleted,
    "check_host": render.host_check,
}
"""How each of the hosts' commands shows its response, by operation."""

HELPS: Final[dict[str, str]] = {
    "hosts": "Hosts: the machines agents run on (this one, and others over SSH).",
}
"""The hosts group's help."""
