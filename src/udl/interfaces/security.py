"""
---------------
- security.py -
---------------

What the servers (the API, and the MCP server) allow, beyond what the CLI does: a request's
output dir only under api.allowed_output_roots, and, when api.api_key is set, only requests that
send it
"""

# System Imports
import hmac
from pathlib import Path

# Local Imports
from udl.config import Settings, expand_path
from udl.errors import NotAllowed, Unauthorized

API_KEY_HEADER = "X-API-Key"


def check_output_dir(settings: Settings, output_dir: Path | None) -> None:
    """Raise NotAllowed unless `output_dir` (a request's) is in one of api.allowed_output_roots.

    `~` and `$VARS` are expanded, a relative dir is taken from the working dir, and `..` and
    links are followed first, so neither can lead out of a root.
    """
    if output_dir is None:
        return
    target = expand_path(output_dir).resolve()
    allowed = [root.resolve() for root in settings.api.allowed_output_roots]
    if not any(target.is_relative_to(root) for root in allowed):
        roots = ", ".join(map(str, allowed)) or "none"
        raise NotAllowed(
            f"Downloading to {target} is not allowed (it is not in api.allowed_output_roots)",
            hint=f"Choose a folder in one of the allowed output roots ({roots}), or leave the "
            "output dir out to use the default one.",
        )


def check_api_key(settings: Settings, given: str | None) -> None:
    """Raise Unauthorized when api.api_key is set and `given` (the request's) is not it."""
    expected = settings.api.api_key
    if expected is None:
        return
    if given is None or not hmac.compare_digest(
        given.encode(), expected.get_secret_value().encode()
    ):
        raise Unauthorized(
            f"This udl server needs its API key, in the {API_KEY_HEADER} header",
            hint="The key is api.api_key in the server's config.",
        )
