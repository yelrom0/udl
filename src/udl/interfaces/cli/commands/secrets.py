"""
--------------
- secrets.py -
--------------

udl secrets: where udl keeps its secrets, and what reads the ones it does not keep. The group's
commands (status, onepassword login) are made from their operations
(udl.interfaces.adapters.add_commands), shown by these renderers.
"""

# System Imports
from collections.abc import Callable
from typing import Final

# Local Imports
from udl.interfaces.cli import render

RENDERERS: Final[dict[str, Callable[..., None]]] = {
    "get_secrets_status": render.secrets_status,
    "set_onepassword_token": render.secrets_status,
}
"""How each of the secrets' commands shows its response, by operation."""

HELPS: Final[dict[str, str]] = {
    "secrets": "Secrets: where udl keeps them, and what reads the ones it does not keep.",
    "secrets/onepassword": "1Password: a secret is read with op when it is used, never kept.",
}
"""The secrets group's help (and its subgroups')."""
