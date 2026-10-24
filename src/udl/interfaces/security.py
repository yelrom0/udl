"""
---------------
- security.py -
---------------

Who the servers (the API, and the MCP server it serves) answer: when api.api_key is set, only
requests that send it. (What they may ask for, such as an output dir under
api.allowed_output_roots, the operations check: udl.core.paths.)
"""

# System Imports
import hmac

# Local Imports
from udl.config import Settings
from udl.errors import Unauthorized

API_KEY_HEADER = "X-API-Key"


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
